from types import SimpleNamespace

from api.app.services.user_metrics.storage.metrics_storage import (
    update_daily_challenges,
)


class _FakeChallengeTable:
    def __init__(self, client):
        self.client = client
        self.operation = None
        self.payload = None

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def upsert(self, payload, **_kwargs):
        self.operation = "upsert"
        self.payload = payload
        return self

    def gt(self, *_args, **_kwargs):
        return self

    def lte(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.operation == "select":
            return SimpleNamespace(data=self.client.active_rows)
        if self.operation == "upsert":
            self.client.upsert_batches.append(self.payload)
            self.client.active_rows = [{"id": row["id"]} for row in self.payload]
            return SimpleNamespace(data=self.payload)
        return SimpleNamespace(data=[])


class _FakeSupabase:
    def __init__(self):
        self.active_rows = []
        self.upsert_batches = []

    def table(self, name):
        assert name == "daily_challenges"
        return _FakeChallengeTable(self)


def test_daily_challenge_generation_is_idempotent_for_the_day():
    supabase = _FakeSupabase()

    update_daily_challenges(supabase)
    update_daily_challenges(supabase)

    assert len(supabase.upsert_batches) == 1
    generated = supabase.upsert_batches[0]
    assert len(generated) == 3
    assert len({row["id"] for row in generated}) == 3
