import uuid
import logging

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from redis.exceptions import LockError, RedisError
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .api_serializers import (
    CompanySerializer,
    ContactSerializer,
    ConversationSerializer,
    KnowledgeBaseSerializer,
    KnowledgeDocumentSerializer,
    MembershipSerializer,
    MessageSerializer,
    UserSerializer,
    WhatsAppNumberSerializer,
)
from .models import (
    Company,
    Contact,
    Conversation,
    KnowledgeBase,
    KnowledgeDocument,
    Membership,
    Message,
    WhatsAppNumber,
)
from .services import conversation_ai_lock, send_whatsapp_text
from .tasks import process_document

User = get_user_model()
logger = logging.getLogger(__name__)


class StandardPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class ConversationMessagePagination(StandardPagination):
    page_size = 50

    def get_page_number(self, request, paginator):
        if request.query_params.get(self.page_query_param) is None:
            return paginator.num_pages or 1
        return super().get_page_number(request, paginator)


def _company_for_request(request, company_id, minimum_role="agent"):
    company = Company.objects.filter(pk=company_id).first()
    if company is None:
        raise NotFound("Empresa não encontrada.")
    if request.user.is_superuser:
        return company, None
    membership = Membership.objects.filter(company=company, user=request.user).first()
    if membership is None:
        raise NotFound("Empresa não encontrada.")
    ranks = {"agent": 0, "admin": 1, "owner": 2}
    if ranks[membership.role] < ranks[minimum_role]:
        raise PermissionDenied("Seu papel não permite esta operação.")
    return company, membership


def _company_list(request, company_id, model, minimum_role="agent"):
    company, _ = _company_for_request(request, company_id, minimum_role)
    return company, model.objects.filter(company=company)


def _validated_assignee(company, user):
    if user and not Membership.objects.filter(company=company, user=user).exists():
        raise ValidationError({"assigned_to_id": "O atendente não pertence a esta empresa."})
    return user


class CsrfCookieView(APIView):
    permission_classes = (AllowAny,)

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        from django.middleware.csrf import get_token

        return Response({"csrfToken": get_token(request)})


class LoginView(APIView):
    permission_classes = (AllowAny,)

    @method_decorator(csrf_protect)
    def post(self, request):
        identifier = str(request.data.get("username", request.data.get("email", ""))).strip()
        password = request.data.get("password", "")
        if not identifier or not password:
            raise ValidationError({"detail": "Informe usuário/e-mail e senha."})
        user = authenticate(request, username=identifier, password=password)
        if user is None and "@" in identifier:
            user_by_email = User.objects.filter(email__iexact=identifier).first()
            if user_by_email:
                user = authenticate(request, username=user_by_email.get_username(), password=password)
        if user is None or not user.is_active:
            raise ValidationError({"detail": "Usuário ou senha inválidos."})
        login(request, user)
        memberships = Membership.objects.filter(user=user).select_related("company")
        companies = [
            {**CompanySerializer(membership.company).data, "role": membership.role}
            for membership in memberships
        ]
        if user.is_superuser:
            companies = [
                {**CompanySerializer(company).data, "role": "owner"}
                for company in Company.objects.all()
            ]
        return Response({"user": UserSerializer(user).data, "companies": companies})


class LogoutView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RefreshView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        request.session.cycle_key()
        return Response({"user": UserSerializer(request.user).data})


class MeView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        memberships = Membership.objects.filter(user=request.user).select_related("company")
        companies = [
            {**CompanySerializer(item.company).data, "role": item.role}
            for item in memberships
        ]
        if request.user.is_superuser:
            companies = [
                {**CompanySerializer(company).data, "role": "owner"}
                for company in Company.objects.all()
            ]
        return Response({"user": UserSerializer(request.user).data, "companies": companies})


class CompanyListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        if request.user.is_superuser:
            companies = Company.objects.all()
            roles = {str(item.pk): "owner" for item in companies}
        else:
            memberships = Membership.objects.filter(user=request.user).select_related("company")
            companies = [item.company for item in memberships]
            roles = {str(item.company_id): item.role for item in memberships}
        return Response(
            [
                {**CompanySerializer(company).data, "role": roles[str(company.pk)]}
                for company in companies
            ]
        )


class CompanyDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id):
        company, membership = _company_for_request(request, company_id)
        data = dict(CompanySerializer(company).data)
        data["role"] = membership.role if membership else "owner"
        data["integrations"] = {
            "whatsapp_configured": bool(settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_APP_SECRET),
            "gemini_configured": bool(settings.GEMINI_API_KEY),
            "webhook_path": "/api/webhooks/whatsapp/",
        }
        return Response(data)


class MembershipListCreateView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id):
        company, _ = _company_for_request(request, company_id)
        memberships = Membership.objects.filter(company=company).select_related("user").order_by("id")
        return Response(MembershipSerializer(memberships, many=True).data)

    def post(self, request, company_id):
        company, _ = _company_for_request(request, company_id, "admin")
        role = request.data.get("role", Membership.Role.AGENT)
        if role not in Membership.Role.values:
            raise ValidationError({"role": "Papel inválido."})
        if role == Membership.Role.OWNER and not request.user.is_superuser:
            raise PermissionDenied("Somente um superusuário pode conceder o papel de owner.")
        user_id = request.data.get("user_id")
        email = request.data.get("email")
        username = request.data.get("username")
        if user_id:
            member_user = generics.get_object_or_404(User, pk=user_id)
        elif email:
            member_user = User.objects.filter(email__iexact=str(email).strip()).first()
        elif username:
            member_user = User.objects.filter(username__iexact=str(username).strip()).first()
        else:
            raise ValidationError({"user_id": "Informe o ID, e-mail ou usuário de uma conta existente."})
        if member_user is None:
            raise ValidationError({"user_id": "Não foi encontrada uma conta com esses dados."})
        membership, created = Membership.objects.get_or_create(
            company=company,
            user=member_user,
            defaults={"role": role},
        )
        if not created:
            raise ValidationError({"user_id": "Este usuário já é membro da empresa."})
        return Response(MembershipSerializer(membership).data, status=status.HTTP_201_CREATED)


class MembershipDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def patch(self, request, company_id, membership_id):
        company, _ = _company_for_request(request, company_id, "admin")
        membership = generics.get_object_or_404(
            Membership.objects.select_related("user"), company=company, pk=membership_id
        )
        if membership.role == Membership.Role.OWNER and not request.user.is_superuser:
            raise PermissionDenied("O papel de owner não pode ser alterado.")
        if "user" in request.data or "user_id" in request.data:
            raise ValidationError({"user": "O membro associado não pode ser alterado."})
        serializer = MembershipSerializer(membership, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data.get("role") == Membership.Role.OWNER and not request.user.is_superuser:
            raise PermissionDenied("Somente um superusuário pode conceder o papel de owner.")
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, company_id, membership_id):
        company, _ = _company_for_request(request, company_id, "admin")
        membership = generics.get_object_or_404(Membership, company=company, pk=membership_id)
        if membership.role == Membership.Role.OWNER:
            raise PermissionDenied("O papel de owner não pode ser removido por esta operação.")
        membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class DashboardView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id):
        company, _ = _company_for_request(request, company_id)
        conversations = Conversation.objects.filter(company=company)
        messages = Message.objects.filter(company=company)
        recent = conversations.select_related("contact", "assigned_to").order_by("-last_message_at", "-created_at")[:8]
        docs = KnowledgeDocument.objects.filter(company=company)
        return Response(
            {
                "total_conversations": conversations.count(),
                "open_conversations": conversations.exclude(status=Conversation.Status.CLOSED).count(),
                "human_conversations": conversations.filter(ai_paused=True).exclude(
                    status=Conversation.Status.CLOSED
                ).count(),
                "ai_conversations": messages.filter(
                    direction=Message.Direction.INBOUND,
                    ai_status=Message.AIStatus.COMPLETED,
                ).values("conversation_id").distinct().count(),
                "inbound_messages": messages.filter(direction=Message.Direction.INBOUND).count(),
                "outbound_messages": messages.filter(direction=Message.Direction.OUTBOUND).count(),
                "documents_processing": docs.filter(
                    status__in=(KnowledgeDocument.Status.PENDING, KnowledgeDocument.Status.PROCESSING)
                ).count(),
                "documents_failed": docs.filter(status=KnowledgeDocument.Status.FAILED).count(),
                "recent_conversations": ConversationSerializer(recent, many=True).data,
            }
        )


class CompanyModelListCreateView(APIView):
    permission_classes = (IsAuthenticated,)
    model = None
    serializer_class = None
    minimum_role = "agent"
    filter_fields = ()
    search_fields = ()

    def get_queryset(self, request, company):
        queryset = self.model.objects.filter(company=company).order_by("pk")
        for field in self.filter_fields:
            value = request.query_params.get(field)
            if value not in (None, ""):
                queryset = queryset.filter(**{field: value})
        search = request.query_params.get("search", "").strip()
        if search and self.search_fields:
            query = Q()
            for field in self.search_fields:
                query |= Q(**{f"{field}__icontains": search})
            queryset = queryset.filter(query)
        return queryset

    def get(self, request, company_id):
        company, _ = _company_for_request(request, company_id, self.minimum_role)
        queryset = self.get_queryset(request, company)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(self.serializer_class(page, many=True).data)

    def post(self, request, company_id):
        company, _ = _company_for_request(request, company_id, self.minimum_role)
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(company=company)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CompanyModelDetailView(APIView):
    permission_classes = (IsAuthenticated,)
    model = None
    serializer_class = None
    minimum_role = "agent"

    def get_object(self, request, company_id, object_id):
        company, _ = _company_for_request(request, company_id, self.minimum_role)
        return company, generics.get_object_or_404(self.model, company=company, pk=object_id)

    def get(self, request, company_id, object_id):
        _, obj = self.get_object(request, company_id, object_id)
        return Response(self.serializer_class(obj).data)

    def patch(self, request, company_id, object_id):
        _, obj = self.get_object(request, company_id, object_id)
        serializer = self.serializer_class(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, company_id, object_id):
        _, obj = self.get_object(request, company_id, object_id)
        try:
            obj.delete()
        except DjangoValidationError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        except IntegrityError as exc:
            raise ValidationError({"detail": "O registro ainda está relacionado a outros dados."}) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


class WhatsAppNumberListView(CompanyModelListCreateView):
    model = WhatsAppNumber
    serializer_class = WhatsAppNumberSerializer
    minimum_role = "admin"

    def get(self, request, company_id):
        company, _ = _company_for_request(request, company_id)
        queryset = self.get_queryset(request, company)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(self.serializer_class(page, many=True).data)


class WhatsAppNumberDetailView(CompanyModelDetailView):
    model = WhatsAppNumber
    serializer_class = WhatsAppNumberSerializer
    minimum_role = "admin"


class ContactListView(CompanyModelListCreateView):
    model = Contact
    serializer_class = ContactSerializer
    filter_fields = ()
    search_fields = ("name", "phone", "wa_id")

    def post(self, request, company_id):
        company, _ = _company_for_request(request, company_id)
        serializer = self.serializer_class(
            data=request.data,
            context={"company": company},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(company=company)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ContactDetailView(CompanyModelDetailView):
    model = Contact
    serializer_class = ContactSerializer


class ContactConversationsView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request, company_id, contact_id):
        company, contacts = _company_list(request, company_id, Contact)
        contact = generics.get_object_or_404(contacts, pk=contact_id)
        conversations = Conversation.objects.filter(company=company, contact=contact).order_by("-last_message_at")
        return Response(ConversationSerializer(conversations, many=True).data)


class ConversationListView(CompanyModelListCreateView):
    model = Conversation
    serializer_class = ConversationSerializer
    filter_fields = ("status", "assigned_to_id", "whatsapp_number_id", "ai_paused")
    search_fields = ("contact__name", "contact__phone", "contact__wa_id")

    def get_queryset(self, request, company):
        queryset = super().get_queryset(request, company)
        if request.query_params.get("status") == "":
            queryset = queryset.exclude(status=Conversation.Status.CLOSED)
        return queryset.select_related("contact", "assigned_to", "whatsapp_number").order_by(
            "-last_message_at", "-created_at"
        )


class ConversationDetailView(CompanyModelDetailView):
    model = Conversation
    serializer_class = ConversationSerializer

    def patch(self, request, company_id, object_id):
        _, conversation = self.get_object(request, company_id, object_id)
        serializer = self.serializer_class(conversation, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        if "assigned_to" in serializer.validated_data:
            serializer.validated_data["assigned_to"] = _validated_assignee(
                conversation.company, serializer.validated_data["assigned_to"]
            )
        serializer.save()
        return Response(serializer.data)


class ConversationMessagesView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (JSONParser,)

    def get_conversation(self, request, company_id, conversation_id):
        company, _ = _company_for_request(request, company_id)
        return generics.get_object_or_404(
            Conversation.objects.select_related("contact", "whatsapp_number"),
            company=company,
            pk=conversation_id,
        )

    def get(self, request, company_id, conversation_id):
        conversation = self.get_conversation(request, company_id, conversation_id)
        queryset = Message.objects.filter(company=conversation.company, conversation=conversation).order_by(
            "created_at", "pk"
        )
        paginator = ConversationMessagePagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(MessageSerializer(page, many=True).data)

    def post(self, request, company_id, conversation_id):
        conversation = self.get_conversation(request, company_id, conversation_id)
        text = request.data.get("text")
        key = request.headers.get("Idempotency-Key", "").strip()
        if not isinstance(text, str) or not text.strip():
            raise ValidationError({"text": "Informe o texto da mensagem."})
        if not key or len(key) > 128:
            raise ValidationError({"Idempotency-Key": "Envie uma chave de idempotência válida (até 128 caracteres)."})
        try:
            lock = conversation_ai_lock(conversation.company_id, conversation.pk, blocking_timeout=15)
            acquired = lock.acquire(blocking=True)
        except RedisError:
            logger.exception("Não foi possível obter o lock de atendimento; conversation_id=%s", conversation.pk)
            return Response(
                {"detail": "O atendimento está temporariamente indisponível. Tente novamente."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not acquired:
            return Response(
                {"detail": "A IA está finalizando uma resposta. Tente enviar novamente em alguns segundos."},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            with transaction.atomic():
                conversation = generics.get_object_or_404(
                    Conversation.objects.select_for_update().select_related("whatsapp_number", "contact"),
                    company_id=conversation.company_id,
                    pk=conversation.pk,
                )
                if conversation.status == Conversation.Status.CLOSED:
                    raise ValidationError({"detail": "Reabra a conversa antes de enviar mensagens."})
                if not conversation.whatsapp_number.is_active:
                    raise ValidationError({"detail": "O número do WhatsApp está inativo."})
                try:
                    outgoing, created = Message.objects.get_or_create(
                        idempotency_key=key,
                        defaults={
                            "company": conversation.company,
                            "conversation": conversation,
                            "external_id": f"manual-pending:{uuid.uuid4()}",
                            "direction": Message.Direction.OUTBOUND,
                            "message_type": "text",
                            "text": text.strip(),
                            "status": Message.Status.PENDING,
                        },
                    )
                except IntegrityError:
                    outgoing = generics.get_object_or_404(Message, idempotency_key=key)
                    created = False
                if not created:
                    if outgoing.company_id != conversation.company_id or outgoing.conversation_id != conversation.pk:
                        raise ValidationError({"Idempotency-Key": "A chave já foi usada em outra conversa."})
                    return Response(MessageSerializer(outgoing).data, status=status.HTTP_200_OK)
                conversation.ai_paused = True
                conversation.ai_paused_at = timezone.now()
                conversation.save(update_fields=("ai_paused", "ai_paused_at"))

            try:
                external_id = send_whatsapp_text(
                    conversation.whatsapp_number, conversation.contact.wa_id, outgoing.text
                )
            except Exception:
                logger.exception("Falha ao enviar mensagem manual; message_id=%s", outgoing.pk)
                outgoing.status = Message.Status.FAILED
                outgoing.save(update_fields=("status",))
                return Response(
                    {"detail": "Não foi possível enviar a mensagem pela integração WhatsApp.", "message": MessageSerializer(outgoing).data},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            outgoing.external_id = external_id
            outgoing.status = Message.Status.SENT
            outgoing.save(update_fields=("external_id", "status"))
            conversation.last_message_at = outgoing.created_at
            conversation.save(update_fields=("last_message_at",))
            return Response(MessageSerializer(outgoing).data, status=status.HTTP_201_CREATED)
        finally:
            try:
                lock.release()
            except LockError:
                logger.warning("O lock do atendimento expirou antes da liberação; conversation_id=%s", conversation.pk)


class ConversationActionView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request, company_id, conversation_id, action):
        company, _ = _company_for_request(request, company_id)
        conversation = generics.get_object_or_404(Conversation, company=company, pk=conversation_id)
        if action in {"pause-ai", "resume-ai"}:
            conversation.ai_paused = action == "pause-ai"
            conversation.ai_paused_at = timezone.now() if conversation.ai_paused else None
            conversation.save(update_fields=("ai_paused", "ai_paused_at"))
        elif action == "assign":
            user_id = request.data.get("user_id")
            user = None if user_id in (None, "") else generics.get_object_or_404(User, pk=user_id)
            conversation.assigned_to = _validated_assignee(company, user)
            conversation.save(update_fields=("assigned_to",))
        elif action in {"close", "reopen"}:
            conversation.status = (
                Conversation.Status.CLOSED if action == "close" else Conversation.Status.OPEN
            )
            conversation.save(update_fields=("status",))
        else:
            raise NotFound("Ação não encontrada.")
        return Response(ConversationSerializer(conversation).data)


class KnowledgeBaseListView(CompanyModelListCreateView):
    model = KnowledgeBase
    serializer_class = KnowledgeBaseSerializer
    minimum_role = "admin"


class KnowledgeBaseDetailView(CompanyModelDetailView):
    model = KnowledgeBase
    serializer_class = KnowledgeBaseSerializer
    minimum_role = "admin"


class KnowledgeDocumentListView(APIView):
    permission_classes = (IsAuthenticated,)
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_base(self, request, company_id, base_id):
        company, _ = _company_for_request(request, company_id, "admin")
        return company, generics.get_object_or_404(KnowledgeBase, company=company, pk=base_id)

    def get(self, request, company_id, base_id):
        company, base = self.get_base(request, company_id, base_id)
        queryset = KnowledgeDocument.objects.filter(company=company, knowledge_base=base)
        doc_status = request.query_params.get("status")
        if doc_status:
            queryset = queryset.filter(status=doc_status)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset.order_by("-created_at"), request, view=self)
        return paginator.get_paginated_response(KnowledgeDocumentSerializer(page, many=True).data)

    def post(self, request, company_id, base_id):
        company, base = self.get_base(request, company_id, base_id)
        serializer = KnowledgeDocumentSerializer(
            data=request.data, context={"company_id": company.pk, "knowledge_base": base}
        )
        serializer.is_valid(raise_exception=True)
        document = serializer.save()
        transaction.on_commit(lambda: process_document.delay(document.pk))
        return Response(KnowledgeDocumentSerializer(document).data, status=status.HTTP_201_CREATED)


class KnowledgeDocumentDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    def get_document(self, request, company_id, base_id, document_id):
        company, _ = _company_for_request(request, company_id, "admin")
        base = generics.get_object_or_404(KnowledgeBase, company=company, pk=base_id)
        return generics.get_object_or_404(
            KnowledgeDocument, company=company, knowledge_base=base, pk=document_id
        )

    def get(self, request, company_id, base_id, document_id):
        document = self.get_document(request, company_id, base_id, document_id)
        return Response(KnowledgeDocumentSerializer(document).data)

    def delete(self, request, company_id, base_id, document_id):
        document = self.get_document(request, company_id, base_id, document_id)
        document.file.delete(save=False)
        document.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class KnowledgeDocumentReprocessView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request, company_id, base_id, document_id):
        document = KnowledgeDocumentDetailView().get_document(
            request, company_id, base_id, document_id
        )
        document.status = KnowledgeDocument.Status.PENDING
        document.error = ""
        document.save(update_fields=("status", "error"))
        transaction.on_commit(lambda: process_document.delay(document.pk))
        return Response(KnowledgeDocumentSerializer(document).data, status=status.HTTP_202_ACCEPTED)
