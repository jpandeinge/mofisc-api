import pytest

from api.app.utils import transaction_model_endpoint as endpoint


def endpoint_prediction(**overrides):
    prediction = {
        "category": "Eating Out",
        "model_category": "FOOD_AND_DRINK",
        "confidence": 0.91,
        "merchant_name": "Shoprite",
        "merchant_similarity": 0.88,
        "merchant_unknown": False,
        "vetoed": False,
        "low_confidence": False,
        "taxonomy": "galahad_v4_2026_07",
        "model_version": "test-model",
    }
    prediction.update(overrides)
    return prediction


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise endpoint.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_hf_pfc_endpoint_uses_structured_contract(monkeypatch):
    monkeypatch.setenv("HF_PFC_ENDPOINT_URL", "https://example.test/model")
    monkeypatch.setenv("HF_PFC_TOKEN", "test-token")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(200, [endpoint_prediction()])

    monkeypatch.setattr(endpoint.requests, "post", fake_post)
    predictions = endpoint.classify_with_pfc_endpoint(
        [
            {
                "transaction_description": "SHOPRITE",
                "transaction_type": "debit",
                "transaction_amount": 100,
            }
        ],
        max_attempts=1,
    )

    assert captured["json"]["inputs"][0]["transaction_amount"] == 100
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert predictions[0].category == "Eating Out"
    assert predictions[0].merchant_name == "Shoprite"


def test_hf_pfc_endpoint_retries_scale_to_zero_response(monkeypatch):
    monkeypatch.setenv("HF_PFC_ENDPOINT_URL", "https://example.test/model")
    monkeypatch.setenv("HF_PFC_TOKEN", "test-token")
    responses = iter(
        [
            FakeResponse(502, {"error": "starting"}),
            FakeResponse(200, [endpoint_prediction()]),
        ]
    )
    sleeps = []
    monkeypatch.setattr(
        endpoint.requests, "post", lambda *_args, **_kwargs: next(responses)
    )
    monkeypatch.setattr(endpoint.time, "sleep", sleeps.append)

    predictions = endpoint.classify_with_pfc_endpoint([{}], max_attempts=2)

    assert predictions[0].model_version == "test-model"
    assert sleeps == [1]


def test_hf_pfc_endpoint_rejects_legacy_label_only_response(monkeypatch):
    monkeypatch.setenv("HF_PFC_ENDPOINT_URL", "https://example.test/model")
    monkeypatch.setenv("HF_PFC_TOKEN", "test-token")
    monkeypatch.setattr(
        endpoint.requests,
        "post",
        lambda *_args, **_kwargs: FakeResponse(
            200,
            [{"label": "Eating Out", "score": 0.9}],
        ),
    )

    with pytest.raises(RuntimeError, match="valid prediction batch"):
        endpoint.classify_with_pfc_endpoint([{}], max_attempts=1)


def test_hf_pfc_endpoint_chunks_large_statements(monkeypatch):
    monkeypatch.setenv("HF_PFC_ENDPOINT_URL", "https://example.test/model")
    monkeypatch.setenv("HF_PFC_TOKEN", "test-token")
    batch_sizes = []

    def fake_post(_url, **kwargs):
        batch = kwargs["json"]["inputs"]
        batch_sizes.append(len(batch))
        return FakeResponse(
            200,
            [
                endpoint_prediction(merchant_name=f"Merchant {item['id']}")
                for item in batch
            ],
        )

    monkeypatch.setattr(endpoint.requests, "post", fake_post)
    predictions = endpoint.classify_with_pfc_endpoint(
        [{"id": index} for index in range(129)],
        max_attempts=1,
    )

    assert batch_sizes == [128, 1]
    assert len(predictions) == 129
    assert predictions[-1].merchant_name == "Merchant 128"


def test_production_can_require_private_hf_endpoint(monkeypatch):
    monkeypatch.setenv("TRANSACTION_MODEL_BACKEND", "hf_endpoint")
    monkeypatch.setenv("PFC_MODEL_REQUIRED", "true")
    monkeypatch.setenv("HF_PFC_ENDPOINT_URL", "https://example.test/model")
    monkeypatch.setenv("HF_PFC_TOKEN", "test-token")

    status = endpoint.require_transaction_model()

    assert status["ready"] is True
    assert status["selected_backend"] == "hf_endpoint"


def test_invalid_transaction_model_backend_fails_configuration(monkeypatch):
    monkeypatch.setenv("TRANSACTION_MODEL_BACKEND", "guess")

    with pytest.raises(ValueError, match="TRANSACTION_MODEL_BACKEND"):
        endpoint.transaction_model_backend()
