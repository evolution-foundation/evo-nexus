"""Tests for int-uptime-kuma client.

Cobre: parser Prometheus, smoke, status/get + filtros, login (incl. regressão do
fallback ok:true), verificação de ack (regressão do `{}` silencioso), payload de
monitor v2 (conditions / accepted_statuscodes), e cada comando de escrita.

Sem rede real (conftest bloqueia). O caminho de escrita usa o fake socketio.
"""
import json
from types import SimpleNamespace

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def make_args(**kw):
    """Build an argparse-like namespace with monitor-arg defaults.

    Defaults para os campos de mutation-safety: execute=True (a maioria dos testes
    de escrita exercita o caminho efetivado; testes de dry-run passam execute=False
    explicitamente) e source válido.
    """
    base = dict(
        name="Test", url="https://example.com/", type="http",
        interval=60, retry_interval=60, maxretries=3,
        headers=None, keyword=None, expected_status=None, id="1",
        execute=True, source="agent:test", confirm=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


SAMPLE_METRICS = """\
# HELP monitor_status The status of the monitor
# TYPE monitor_status gauge
monitor_status{monitor_id="1",monitor_name="Site A",monitor_type="http",monitor_url="https://a.com"} 1
monitor_status{monitor_id="2",monitor_name="Site B",monitor_type="keyword",monitor_url="https://b.com"} 0
monitor_status{monitor_id="3",monitor_name="Grp",monitor_type="group",monitor_url="null"} 2
monitor_response_time{monitor_id="1",monitor_name="Site A",monitor_type="http",monitor_url="https://a.com"} 123
monitor_uptime_ratio{monitor_id="1",monitor_name="Site A",monitor_type="http",monitor_url="https://a.com"} 0.997
monitor_uptime_ratio{monitor_id="1",monitor_name="Site A",monitor_type="http",monitor_url="https://a.com",window="24"} 0.5
monitor_cert_days_remaining{monitor_id="1",monitor_name="Site A",monitor_type="http",monitor_url="https://a.com"} 78
monitor_cert_is_valid{monitor_id="1",monitor_name="Site A",monitor_type="http",monitor_url="https://a.com"} 1
monitor_cert_is_valid{monitor_id="2",monitor_name="Site B",monitor_type="keyword",monitor_url="https://b.com"} 0
"""


# ── Prometheus parser ─────────────────────────────────────────────────────────

class TestParseMetrics:
    def test_returns_list(self, uk):
        assert isinstance(uk._parse_metrics(SAMPLE_METRICS), list)

    def test_monitor_count(self, uk):
        assert len(uk._parse_metrics(SAMPLE_METRICS)) == 3

    def test_sorted_by_id(self, uk):
        ids = [m["id"] for m in uk._parse_metrics(SAMPLE_METRICS)]
        assert ids == ["1", "2", "3"]

    def test_status_up(self, uk):
        m = next(m for m in uk._parse_metrics(SAMPLE_METRICS) if m["id"] == "1")
        assert m["status"] == "UP"

    def test_status_down(self, uk):
        m = next(m for m in uk._parse_metrics(SAMPLE_METRICS) if m["id"] == "2")
        assert m["status"] == "DOWN"

    def test_status_pending(self, uk):
        m = next(m for m in uk._parse_metrics(SAMPLE_METRICS) if m["id"] == "3")
        assert m["status"] == "PENDING"

    def test_name_extracted(self, uk):
        m = next(m for m in uk._parse_metrics(SAMPLE_METRICS) if m["id"] == "1")
        assert m["name"] == "Site A"

    def test_type_extracted(self, uk):
        m = next(m for m in uk._parse_metrics(SAMPLE_METRICS) if m["id"] == "2")
        assert m["type"] == "keyword"

    def test_url_extracted(self, uk):
        m = next(m for m in uk._parse_metrics(SAMPLE_METRICS) if m["id"] == "1")
        assert m["url"] == "https://a.com"

    def test_response_ms(self, uk):
        m = next(m for m in uk._parse_metrics(SAMPLE_METRICS) if m["id"] == "1")
        assert m["response_ms"] == 123

    def test_uptime_24h_formatted(self, uk):
        m = next(m for m in uk._parse_metrics(SAMPLE_METRICS) if m["id"] == "1")
        assert m["uptime_24h"] == "99.7%"

    def test_windowed_uptime_skipped(self, uk):
        # the window="24" line (0.5) must NOT override the base 0.997
        m = next(m for m in uk._parse_metrics(SAMPLE_METRICS) if m["id"] == "1")
        assert m["uptime_24h"] == "99.7%"

    def test_cert_days(self, uk):
        m = next(m for m in uk._parse_metrics(SAMPLE_METRICS) if m["id"] == "1")
        assert m["cert_days"] == 78

    def test_cert_valid_true(self, uk):
        m = next(m for m in uk._parse_metrics(SAMPLE_METRICS) if m["id"] == "1")
        assert m["cert_valid"] is True

    def test_cert_valid_false(self, uk):
        m = next(m for m in uk._parse_metrics(SAMPLE_METRICS) if m["id"] == "2")
        assert m["cert_valid"] is False

    def test_comment_lines_ignored(self, uk):
        # HELP/TYPE comment lines must not create monitors
        assert all(m["id"].isdigit() for m in uk._parse_metrics(SAMPLE_METRICS))

    def test_empty_input(self, uk):
        assert uk._parse_metrics("") == []

    def test_unknown_metric_ignored(self, uk):
        raw = 'some_other_metric{monitor_id="9"} 1'
        assert uk._parse_metrics(raw) == []

    def test_malformed_value_ignored(self, uk):
        raw = 'monitor_status{monitor_id="1",monitor_name="X",monitor_type="http"} notanumber'
        # monitor dict is created but status not set (value unparseable)
        out = uk._parse_metrics(raw)
        assert out and "status" not in out[0]


# ── smoke ─────────────────────────────────────────────────────────────────────

class TestSmoke:
    @pytest.fixture(autouse=True)
    def _stub_write_auth(self, uk, monkeypatch, request):
        """The read-path smoke tests isolate the read path: stub write_auth to a
        no-network PASS. The dedicated write_auth tests below opt OUT of this stub
        (they exercise the real _smoke_write_auth via the fake socketio)."""
        if request.function.__name__.startswith("test_smoke_write_auth"):
            return
        monkeypatch.setattr(
            uk, "_smoke_write_auth",
            lambda: {"step": "write_auth", "status": "PASS", "duration_ms": 0},
        )

    def test_smoke_pass(self, uk, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        with pytest.raises(SystemExit) as e:
            uk.cmd_smoke()
        assert e.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["overall"] == "PASS"

    def test_smoke_exit_zero_always(self, uk, monkeypatch, capsys):
        def _boom():
            raise RuntimeError("metrics down")
        monkeypatch.setattr(uk, "_fetch_metrics", _boom)
        with pytest.raises(SystemExit) as e:
            uk.cmd_smoke()
        assert e.value.code == 0  # exit 0 even on failure (smoke contract)

    def test_smoke_fail_overall_on_metrics_error(self, uk, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        with pytest.raises(SystemExit):
            uk.cmd_smoke()
        out = json.loads(capsys.readouterr().out)
        assert out["overall"] == "FAIL"

    def test_smoke_json_shape(self, uk, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        with pytest.raises(SystemExit):
            uk.cmd_smoke()
        out = json.loads(capsys.readouterr().out)
        assert set(["overall", "steps", "duration_ms"]).issubset(out)
        assert isinstance(out["steps"], list)

    def test_smoke_missing_api_key(self, uk, monkeypatch, capsys):
        monkeypatch.setattr(uk, "API_KEY", "")
        with pytest.raises(SystemExit) as e:
            uk.cmd_smoke()
        assert e.value.code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["overall"] == "FAIL"
        assert out["steps"][0]["step"] == "auth"
        assert out["steps"][0]["status"] == "FAIL"

    def test_smoke_counts_down(self, uk, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        with pytest.raises(SystemExit):
            uk.cmd_smoke()
        out = json.loads(capsys.readouterr().out)
        fetch = next(s for s in out["steps"] if s["step"] == "fetch_metrics")
        assert fetch["down"] == 1

    # ── smoke v2: write_auth step ──────────────────────────────────────────────

    def test_smoke_write_auth_pass(self, uk, monkeypatch, fake_socketio, capsys):
        """write_auth does a real login (no mutation) and reports PASS."""
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        fake_socketio.acks["login"] = {"ok": True, "token": "t"}
        with pytest.raises(SystemExit) as e:
            uk.cmd_smoke()
        assert e.value.code == 0
        out = json.loads(capsys.readouterr().out)
        wa = next(s for s in out["steps"] if s["step"] == "write_auth")
        assert wa["status"] == "PASS"
        assert out["overall"] == "PASS"
        # it must NOT mutate: only login was emitted
        events = [c[0] for c in fake_socketio.last_instance.calls]
        assert events == ["login"]

    def test_smoke_write_auth_fail_sets_overall(self, uk, monkeypatch, fake_socketio, capsys):
        """Rejected login → write_auth FAIL → overall FAIL (the Fase 1 regression)."""
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        fake_socketio.acks["login"] = {"ok": False, "msg": "authIncorrectCreds"}
        with pytest.raises(SystemExit) as e:
            uk.cmd_smoke()
        assert e.value.code == 0  # smoke always exits 0
        out = json.loads(capsys.readouterr().out)
        wa = next(s for s in out["steps"] if s["step"] == "write_auth")
        assert wa["status"] == "FAIL"
        assert out["overall"] == "FAIL"

    def test_smoke_write_auth_skip_no_creds(self, uk, monkeypatch, capsys):
        """No write creds → SKIP, and SKIP must NOT fail overall."""
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        monkeypatch.setattr(uk, "USERNAME", "")
        with pytest.raises(SystemExit):
            uk.cmd_smoke()
        out = json.loads(capsys.readouterr().out)
        wa = next(s for s in out["steps"] if s["step"] == "write_auth")
        assert wa["status"] == "SKIP"
        assert out["overall"] == "PASS"  # read path fine, write skipped

    def test_smoke_write_auth_skip_no_socketio(self, uk, monkeypatch, capsys):
        """python-socketio missing → SKIP (graceful), never crash."""
        import builtins
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        real_import = builtins.__import__

        def _no_socketio(name, *a, **k):
            if name == "socketio":
                raise ImportError("no module named socketio")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_socketio)
        with pytest.raises(SystemExit) as e:
            uk.cmd_smoke()
        assert e.value.code == 0
        out = json.loads(capsys.readouterr().out)
        wa = next(s for s in out["steps"] if s["step"] == "write_auth")
        assert wa["status"] == "SKIP"


# ── status / get ──────────────────────────────────────────────────────────────

class TestStatusGet:
    def test_status_all(self, uk, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        uk.cmd_status(make_args(name=None, down=False))
        assert len(json.loads(capsys.readouterr().out)) == 3

    def test_status_name_filter(self, uk, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        uk.cmd_status(make_args(name="site a", down=False))
        out = json.loads(capsys.readouterr().out)
        assert len(out) == 1 and out[0]["name"] == "Site A"

    def test_status_down_filter(self, uk, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        uk.cmd_status(make_args(name=None, down=True))
        out = json.loads(capsys.readouterr().out)
        assert len(out) == 1 and out[0]["status"] == "DOWN"

    def test_get_found(self, uk, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        uk.cmd_get(make_args(id="2"))
        out = json.loads(capsys.readouterr().out)
        assert out["id"] == "2"

    def test_get_not_found(self, uk, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        with pytest.raises(SystemExit) as e:
            uk.cmd_get(make_args(id="999"))
        assert e.value.code == 1
        assert "not found" in capsys.readouterr().out


# ── login (regressão: fallback ok:true) ───────────────────────────────────────

class TestLogin:
    def test_login_success(self, uk, fake_socketio):
        fake_socketio.acks["login"] = {"ok": True, "token": "tok123"}
        s = uk.UKSocket()
        s.connect()
        resp = s.login()
        assert resp["ok"] is True

    def test_login_failure_raises(self, uk, fake_socketio):
        """REGRESSÃO: login com ok:false DEVE levantar, nunca seguir."""
        fake_socketio.acks["login"] = {"ok": False, "msg": "authIncorrectCreds"}
        s = uk.UKSocket()
        s.connect()
        with pytest.raises(RuntimeError) as e:
            s.login()
        assert "login failed" in str(e.value)
        assert "authIncorrectCreds" in str(e.value)

    def test_login_empty_response_raises(self, uk, fake_socketio):
        """REGRESSÃO: resposta vazia/não-dict não vira fallback ok:true."""
        fake_socketio.acks["login"] = {}
        s = uk.UKSocket()
        s.connect()
        with pytest.raises(RuntimeError):
            s.login()

    def test_login_none_response_raises(self, uk, fake_socketio):
        fake_socketio.acks["login"] = None
        s = uk.UKSocket()
        s.connect()
        with pytest.raises(RuntimeError):
            s.login()

    def test_connect_retries_then_succeeds(self, uk, fake_socketio):
        fake_socketio.connect_should_fail = 2
        s = uk.UKSocket()
        s.connect(attempts=4)  # should retry past the 2 failures
        assert s._sio is not None

    def test_connect_exhausts_retries(self, uk, fake_socketio):
        fake_socketio.connect_should_fail = 10
        s = uk.UKSocket()
        with pytest.raises(RuntimeError) as e:
            s.connect(attempts=3)
        assert "connect failed" in str(e.value)


# ── _login_hint (auth failure is actionable, not opaque) ──────────────────────

class TestLoginHint:
    def test_bad_creds_hint(self, uk):
        h = uk._login_hint({"ok": False, "msg": "authIncorrectCreds"})
        assert "stale" in h or "incorretos" in h
        assert "authIncorrectCreds" in h  # raw payload preserved

    def test_2fa_hint(self, uk):
        h = uk._login_hint({"ok": False, "msg": "Token required for 2FA"})
        assert "2FA" in h

    def test_unknown_hint_keeps_raw(self, uk):
        h = uk._login_hint({"ok": False, "msg": "weird"})
        assert "weird" in h

    def test_non_dict_hint(self, uk):
        h = uk._login_hint(None)
        assert "null" in h  # json.dumps(None) -> "null"

    def test_login_failure_message_is_actionable(self, uk, fake_socketio):
        """The raised error must carry the actionable hint, not just the raw ack."""
        fake_socketio.acks["login"] = {"ok": False, "msg": "authIncorrectCreds"}
        s = uk.UKSocket()
        s.connect()
        with pytest.raises(RuntimeError) as e:
            s.login()
        assert "stale" in str(e.value) or "incorretos" in str(e.value)


# ── _socket_session (auth gate) ───────────────────────────────────────────────

class TestSocketSession:
    def test_session_exits_on_bad_login(self, uk, fake_socketio, capsys):
        fake_socketio.acks["login"] = {"ok": False, "msg": "authIncorrectCreds"}
        with pytest.raises(SystemExit) as e:
            uk._socket_session()
        assert e.value.code == 1
        assert "authIncorrectCreds" in capsys.readouterr().out

    def test_session_exits_on_missing_creds(self, uk, monkeypatch, capsys):
        monkeypatch.setattr(uk, "USERNAME", "")
        with pytest.raises(SystemExit) as e:
            uk._socket_session()
        assert e.value.code == 1

    def test_session_ok_returns_socket(self, uk, fake_socketio):
        fake_socketio.acks["login"] = {"ok": True, "token": "t"}
        s = uk._socket_session()
        assert isinstance(s, uk.UKSocket)


# ── _check_ack (regressão: `{}` silencioso) ───────────────────────────────────

class TestCheckAck:
    def test_ok_passes_through(self, uk):
        resp = {"ok": True, "monitorID": 5}
        assert uk._check_ack(resp) == resp

    def test_empty_dict_exits_nonzero(self, uk, capsys):
        """REGRESSÃO: ack {} NÃO pode passar como sucesso."""
        with pytest.raises(SystemExit) as e:
            uk._check_ack({})
        assert e.value.code == 1
        assert "failed" in capsys.readouterr().out

    def test_ok_false_exits_nonzero(self, uk, capsys):
        with pytest.raises(SystemExit) as e:
            uk._check_ack({"ok": False, "msg": "boom"})
        assert e.value.code == 1
        assert "boom" in capsys.readouterr().out

    def test_non_dict_exits_nonzero(self, uk):
        with pytest.raises(SystemExit) as e:
            uk._check_ack("not a dict")
        assert e.value.code == 1


# ── _build_monitor (schema v2) ────────────────────────────────────────────────

class TestBuildMonitor:
    def test_has_conditions_field(self, uk):
        """v2 schema: conditions é NOT NULL."""
        m = uk._build_monitor(make_args())
        assert m["conditions"] == []

    def test_accepted_statuscodes_default_list(self, uk):
        """o servidor itera accepted_statuscodes (.every) — deve ser lista."""
        m = uk._build_monitor(make_args())
        assert m["accepted_statuscodes"] == ["200-299"]

    def test_accepted_statuscodes_custom(self, uk):
        m = uk._build_monitor(make_args(expected_status=204))
        assert m["accepted_statuscodes"] == ["204"]

    def test_accepted_statuscodes_zero_not_dropped(self, uk):
        """MEDIUM-1: expected_status=0 is a real value, not a 'use default' signal."""
        m = uk._build_monitor(make_args(expected_status=0))
        assert m["accepted_statuscodes"] == ["0"]

    def test_basic_fields(self, uk):
        m = uk._build_monitor(make_args(name="X", url="https://x.com"))
        assert m["name"] == "X" and m["url"] == "https://x.com"
        assert m["type"] == "http" and m["active"] is True

    def test_interval_passthrough(self, uk):
        m = uk._build_monitor(make_args(interval=120, maxretries=5))
        assert m["interval"] == 120 and m["maxretries"] == 5

    def test_with_id_for_edit(self, uk):
        m = uk._build_monitor(make_args(id="7"), with_id=True)
        assert m["id"] == 7

    def test_without_id_for_add(self, uk):
        m = uk._build_monitor(make_args())
        assert "id" not in m

    def test_headers_parsed(self, uk):
        m = uk._build_monitor(make_args(headers='{"apikey":"abc"}'))
        assert m["headers"] == {"apikey": "abc"}

    def test_headers_invalid_json_exits(self, uk, capsys):
        with pytest.raises(SystemExit) as e:
            uk._build_monitor(make_args(headers="{bad json"))
        assert e.value.code == 1
        assert "invalid --headers" in capsys.readouterr().out

    def test_keyword_set(self, uk):
        m = uk._build_monitor(make_args(keyword='"state":"open"'))
        assert m["keyword"] == '"state":"open"'

    def test_keyword_absent_when_none(self, uk):
        m = uk._build_monitor(make_args(keyword=None))
        assert "keyword" not in m

    def test_resend_interval_default(self, uk):
        m = uk._build_monitor(make_args())
        assert m["resendInterval"] == 0

    def test_method_default_get(self, uk):
        assert uk._build_monitor(make_args())["method"] == "GET"


# ── write commands ────────────────────────────────────────────────────────────

class TestWriteCommands:
    def _ok(self, fake, event, **extra):
        fake.acks["login"] = {"ok": True, "token": "t"}
        fake.acks[event] = {"ok": True, **extra}

    def test_add_success(self, uk, fake_socketio, capsys):
        self._ok(fake_socketio, "add", monitorID=99, msg="successAdded")
        uk.cmd_add(make_args(name="N", url="https://n.com"))
        out = json.loads(capsys.readouterr().out)
        assert out["monitorID"] == 99

    def test_add_emits_add_event(self, uk, fake_socketio, capsys):
        self._ok(fake_socketio, "add", monitorID=1)
        uk.cmd_add(make_args())
        events = [c[0] for c in fake_socketio.last_instance.calls]
        assert "add" in events

    def test_add_payload_has_conditions(self, uk, fake_socketio, capsys):
        self._ok(fake_socketio, "add", monitorID=1)
        uk.cmd_add(make_args())
        add_call = next(c for c in fake_socketio.last_instance.calls if c[0] == "add")
        payload = add_call[1][0]
        assert payload["conditions"] == []

    def test_add_failure_exits(self, uk, fake_socketio, capsys):
        """REGRESSÃO end-to-end: add com erro do servidor exits != 0."""
        fake_socketio.acks["login"] = {"ok": True, "token": "t"}
        fake_socketio.acks["add"] = {"ok": False, "msg": "schema error"}
        with pytest.raises(SystemExit) as e:
            uk.cmd_add(make_args())
        assert e.value.code == 1
        assert "schema error" in capsys.readouterr().out

    def test_add_empty_ack_exits(self, uk, fake_socketio, capsys):
        fake_socketio.acks["login"] = {"ok": True, "token": "t"}
        fake_socketio.acks["add"] = {}
        with pytest.raises(SystemExit) as e:
            uk.cmd_add(make_args())
        assert e.value.code == 1

    def test_add_disconnects(self, uk, fake_socketio, capsys):
        self._ok(fake_socketio, "add", monitorID=1)
        uk.cmd_add(make_args())
        assert getattr(fake_socketio.last_instance, "disconnected", False)

    def test_add_disconnects_on_failure(self, uk, fake_socketio, capsys):
        fake_socketio.acks["login"] = {"ok": True, "token": "t"}
        fake_socketio.acks["add"] = {"ok": False}
        with pytest.raises(SystemExit):
            uk.cmd_add(make_args())
        assert getattr(fake_socketio.last_instance, "disconnected", False)

    def test_edit_success(self, uk, fake_socketio, capsys):
        self._ok(fake_socketio, "editMonitor", msg="successEdited")
        uk.cmd_edit(make_args(id="3"))
        events = [c[0] for c in fake_socketio.last_instance.calls]
        assert "editMonitor" in events

    def test_edit_payload_has_id(self, uk, fake_socketio, capsys):
        self._ok(fake_socketio, "editMonitor")
        uk.cmd_edit(make_args(id="3"))
        call = next(c for c in fake_socketio.last_instance.calls if c[0] == "editMonitor")
        assert call[1][0]["id"] == 3

    def test_pause_success(self, uk, fake_socketio, capsys):
        self._ok(fake_socketio, "pauseMonitor")
        uk.cmd_pause(make_args(id="3"))
        call = next(c for c in fake_socketio.last_instance.calls if c[0] == "pauseMonitor")
        assert call[1][0] == 3

    def test_resume_success(self, uk, fake_socketio, capsys):
        self._ok(fake_socketio, "resumeMonitor")
        uk.cmd_resume(make_args(id="4"))
        call = next(c for c in fake_socketio.last_instance.calls if c[0] == "resumeMonitor")
        assert call[1][0] == 4

    def test_delete_success(self, uk, fake_socketio, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_snapshot_monitor", lambda i: {"id": "5", "name": "Doomed"})
        self._ok(fake_socketio, "deleteMonitor", msg="successDeleted")
        uk.cmd_delete(make_args(id="5", confirm="Doomed"))
        call = next(c for c in fake_socketio.last_instance.calls if c[0] == "deleteMonitor")
        assert call[1][0] == 5

    def test_delete_failure_exits(self, uk, fake_socketio, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_snapshot_monitor", lambda i: {"id": "5", "name": "Doomed"})
        fake_socketio.acks["login"] = {"ok": True, "token": "t"}
        fake_socketio.acks["deleteMonitor"] = {"ok": False, "msg": "nope"}
        with pytest.raises(SystemExit) as e:
            uk.cmd_delete(make_args(id="5", confirm="Doomed"))
        assert e.value.code == 1

    # HIGH-1: ack barrier must be enforced on ALL 5 writes, not just add/delete.
    # These are non-tautological: the fake call() returns {"ok":True} by default,
    # so an ok:false ack only exits non-zero if _check_ack actually runs. Neutralize
    # _check_ack and these flip to exit-0 → tests fail. That's the regression guard.
    def test_edit_failure_exits(self, uk, fake_socketio, capsys):
        fake_socketio.acks["login"] = {"ok": True, "token": "t"}
        fake_socketio.acks["editMonitor"] = {"ok": False, "msg": "edit boom"}
        with pytest.raises(SystemExit) as e:
            uk.cmd_edit(make_args(id="3"))
        assert e.value.code == 1
        assert "edit boom" in capsys.readouterr().out

    def test_pause_failure_exits(self, uk, fake_socketio, capsys):
        fake_socketio.acks["login"] = {"ok": True, "token": "t"}
        fake_socketio.acks["pauseMonitor"] = {"ok": False, "msg": "pause boom"}
        with pytest.raises(SystemExit) as e:
            uk.cmd_pause(make_args(id="3"))
        assert e.value.code == 1
        assert "pause boom" in capsys.readouterr().out

    def test_resume_failure_exits(self, uk, fake_socketio, capsys):
        fake_socketio.acks["login"] = {"ok": True, "token": "t"}
        fake_socketio.acks["resumeMonitor"] = {"ok": False, "msg": "resume boom"}
        with pytest.raises(SystemExit) as e:
            uk.cmd_resume(make_args(id="4"))
        assert e.value.code == 1
        assert "resume boom" in capsys.readouterr().out

    def test_pause_id_coerced_to_int(self, uk, fake_socketio, capsys):
        self._ok(fake_socketio, "pauseMonitor")
        uk.cmd_pause(make_args(id="42"))
        call = next(c for c in fake_socketio.last_instance.calls if c[0] == "pauseMonitor")
        assert isinstance(call[1][0], int)


# ── mutation-safety: dry-run default, --source, gates, snapshot, --max-writes ──

class TestSourceValidation:
    @pytest.mark.parametrize("src", ["human", "agent:kuma-gates", "routine:nightly-sync"])
    def test_valid_sources(self, uk, src):
        assert uk.validate_source(src) == src

    @pytest.mark.parametrize("src", [None, "", "robot", "agent:", "agent:UPPER", "human ", "x;y"])
    def test_invalid_sources_exit(self, uk, src, capsys):
        with pytest.raises(SystemExit) as e:
            uk.validate_source(src)
        assert e.value.code == 1
        assert "--source obrigatório" in capsys.readouterr().err


class TestDryRunDefault:
    """Sem --execute: nenhum write efetiva, nenhum socket é aberto."""

    def _no_socket(self, uk, monkeypatch):
        """Falha alta se qualquer caminho tentar abrir uma sessão de escrita."""
        def boom():
            raise AssertionError("dry-run NÃO pode abrir socket de escrita")
        monkeypatch.setattr(uk, "_socket_session", boom)

    def test_add_dry_run_no_socket(self, uk, monkeypatch, capsys):
        self._no_socket(uk, monkeypatch)
        uk.cmd_add(make_args(execute=False, name="N", url="https://n.com"))
        out = json.loads(capsys.readouterr().out)
        assert out["dry_run"] is True and out["would"] == "add"
        assert out["monitor"]["name"] == "N"

    def test_edit_dry_run_no_socket(self, uk, monkeypatch, capsys):
        self._no_socket(uk, monkeypatch)
        monkeypatch.setattr(uk, "_snapshot_monitor", lambda i: {"id": "3", "name": "Old"})
        uk.cmd_edit(make_args(execute=False, id="3"))
        out = json.loads(capsys.readouterr().out)
        assert out["dry_run"] is True and out["would"] == "edit"
        assert out["snapshot_before"]["name"] == "Old"

    def test_pause_dry_run_no_socket(self, uk, monkeypatch, capsys):
        self._no_socket(uk, monkeypatch)
        uk.cmd_pause(make_args(execute=False, id="7"))
        out = json.loads(capsys.readouterr().out)
        assert out["dry_run"] is True and out["would"] == "pause" and out["id"] == 7
        assert "warning" in out

    def test_resume_dry_run_no_socket(self, uk, monkeypatch, capsys):
        self._no_socket(uk, monkeypatch)
        uk.cmd_resume(make_args(execute=False, id="8"))
        out = json.loads(capsys.readouterr().out)
        assert out["dry_run"] is True and out["would"] == "resume" and out["id"] == 8

    def test_delete_dry_run_no_socket(self, uk, monkeypatch, capsys):
        self._no_socket(uk, monkeypatch)
        monkeypatch.setattr(uk, "_snapshot_monitor", lambda i: {"id": "9", "name": "Doomed"})
        uk.cmd_delete(make_args(execute=False, id="9"))
        out = json.loads(capsys.readouterr().out)
        assert out["dry_run"] is True and out["would"] == "delete"
        assert out["snapshot_before"]["name"] == "Doomed"
        assert "IRREVERSÍVEL" in out["warning"]

    def test_dry_run_does_not_check_ack(self, uk, monkeypatch, capsys):
        """Dry-run não chama _check_ack (não há resposta de servidor a validar)."""
        self._no_socket(uk, monkeypatch)
        def boom(_resp):
            raise AssertionError("_check_ack não deve rodar em dry-run")
        monkeypatch.setattr(uk, "_check_ack", boom)
        uk.cmd_add(make_args(execute=False))
        assert json.loads(capsys.readouterr().out)["dry_run"] is True

    def test_dry_run_requires_source(self, uk, monkeypatch, capsys):
        """Mesmo em dry-run, --source é validado primeiro."""
        self._no_socket(uk, monkeypatch)
        with pytest.raises(SystemExit) as e:
            uk.cmd_add(make_args(execute=False, source=None))
        assert e.value.code == 1


class TestExecuteMutates:
    """Com --execute: efetiva e respeita o ack-check (Fase 1 preservada)."""

    def _ok(self, fake, event, **extra):
        fake.acks["login"] = {"ok": True, "token": "t"}
        fake.acks[event] = {"ok": True, **extra}

    def test_add_execute_emits_event(self, uk, fake_socketio, capsys):
        self._ok(fake_socketio, "add", monitorID=5)
        uk.cmd_add(make_args(execute=True))
        events = [c[0] for c in fake_socketio.last_instance.calls]
        assert "add" in events
        assert json.loads(capsys.readouterr().out)["dry_run"] is False

    def test_add_execute_respects_ack(self, uk, fake_socketio, capsys):
        fake_socketio.acks["login"] = {"ok": True, "token": "t"}
        fake_socketio.acks["add"] = {"ok": False, "msg": "schema error"}
        with pytest.raises(SystemExit) as e:
            uk.cmd_add(make_args(execute=True))
        assert e.value.code == 1

    def test_resume_execute_respects_ack(self, uk, fake_socketio, capsys):
        fake_socketio.acks["login"] = {"ok": True, "token": "t"}
        fake_socketio.acks["resumeMonitor"] = {"ok": False, "msg": "boom"}
        with pytest.raises(SystemExit) as e:
            uk.cmd_resume(make_args(execute=True, id="3"))
        assert e.value.code == 1


class TestDeleteGate:
    """delete exige --execute + --confirm com o nome real do monitor (2º fator)."""

    def _ok_delete(self, fake):
        fake.acks["login"] = {"ok": True, "token": "t"}
        fake.acks["deleteMonitor"] = {"ok": True, "msg": "successDeleted"}

    def test_execute_without_confirm_blocks(self, uk, fake_socketio, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_snapshot_monitor", lambda i: {"id": "5", "name": "Real"})
        self._ok_delete(fake_socketio)
        called = {"socket": False}
        monkeypatch.setattr(uk, "_socket_session",
                            lambda: called.__setitem__("socket", True))
        with pytest.raises(SystemExit) as e:
            uk.cmd_delete(make_args(execute=True, id="5", confirm=None))
        assert e.value.code == 1
        assert "--confirm" in capsys.readouterr().err
        assert called["socket"] is False  # gate barrou antes de abrir socket

    def test_confirm_name_mismatch_blocks(self, uk, fake_socketio, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_snapshot_monitor", lambda i: {"id": "5", "name": "Real"})
        self._ok_delete(fake_socketio)
        with pytest.raises(SystemExit) as e:
            uk.cmd_delete(make_args(execute=True, id="5", confirm="Wrong"))
        assert e.value.code == 1
        assert "não bate" in capsys.readouterr().err

    def test_confirm_match_allows_delete(self, uk, fake_socketio, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_snapshot_monitor", lambda i: {"id": "5", "name": "Real"})
        self._ok_delete(fake_socketio)
        uk.cmd_delete(make_args(execute=True, id="5", confirm="Real"))
        call = next(c for c in fake_socketio.last_instance.calls if c[0] == "deleteMonitor")
        assert call[1][0] == 5

    def test_no_snapshot_blocks_delete(self, uk, fake_socketio, monkeypatch, capsys):
        """Sem snapshot (monitor pausado/ausente) o 2º fator não confirma → bloqueia."""
        monkeypatch.setattr(uk, "_snapshot_monitor", lambda i: None)
        self._ok_delete(fake_socketio)
        with pytest.raises(SystemExit) as e:
            uk.cmd_delete(make_args(execute=True, id="5", confirm="Whatever"))
        assert e.value.code == 1
        assert "snapshot indisponível" in capsys.readouterr().err


class TestSnapshotBefore:
    """edit/delete capturam snapshot_before via Prometheus, graceful em falha."""

    def test_snapshot_finds_monitor(self, uk, monkeypatch):
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        snap = uk._snapshot_monitor("2")
        assert snap["name"] == "Site B"

    def test_snapshot_missing_returns_none(self, uk, monkeypatch):
        monkeypatch.setattr(uk, "_fetch_metrics", lambda: SAMPLE_METRICS)
        assert uk._snapshot_monitor("999") is None

    def test_snapshot_network_error_returns_none(self, uk, monkeypatch):
        def boom():
            raise RuntimeError("network down")
        monkeypatch.setattr(uk, "_fetch_metrics", boom)
        assert uk._snapshot_monitor("1") is None

    def test_edit_execute_includes_snapshot(self, uk, fake_socketio, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_snapshot_monitor", lambda i: {"id": "3", "name": "Before"})
        fake_socketio.acks["login"] = {"ok": True, "token": "t"}
        fake_socketio.acks["editMonitor"] = {"ok": True}
        uk.cmd_edit(make_args(execute=True, id="3"))
        out = json.loads(capsys.readouterr().out)
        assert out["snapshot_before"]["name"] == "Before" and out["dry_run"] is False


class TestMaxWrites:
    def test_write_limit_blocks_after_max(self, uk, monkeypatch, capsys):
        monkeypatch.setattr(uk, "_write_count", 0)
        monkeypatch.setattr(uk, "_max_writes", 1)
        uk.check_write_limit()  # 1st ok → count = 1
        with pytest.raises(SystemExit) as e:
            uk.check_write_limit()  # 2nd → over limit
        assert e.value.code == 1
        assert "Limite de" in capsys.readouterr().err
