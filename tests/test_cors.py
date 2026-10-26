def test_allowed_origin_receives_cors_headers(client, settings):
    settings.CORS_ALLOWED_ORIGINS = ["http://192.168.0.7:8080"]

    response = client.options(
        "/api/v1/auth/me/",
        HTTP_ORIGIN="http://192.168.0.7:8080",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
    )

    assert response.status_code == 204
    assert response["Access-Control-Allow-Origin"] == "http://192.168.0.7:8080"
    assert response["Access-Control-Allow-Credentials"] == "true"
    assert "X-CSRFToken" in response["Access-Control-Allow-Headers"]


def test_unknown_origin_does_not_receive_cors_headers(client, settings):
    settings.CORS_ALLOWED_ORIGINS = ["https://app.gestcloud.com.br"]

    response = client.get(
        "/api/v1/auth/me/",
        HTTP_ORIGIN="https://malicious.example",
    )

    assert "Access-Control-Allow-Origin" not in response
