"""Notification channel "E-mail": form ↔ Apprise mailto URL, the password never goes back to the browser."""
import pytest


@pytest.fixture
def nf(app_module):
    from app import notify
    return notify


def test_relay_without_login(nf):
    url = nf.email_url({"host": "smtp.example.com", "security": "none", "sender": "tc@example.com", "to": "a@example.com"})
    assert url == "mailto://smtp.example.com:25?from=tc@example.com&to=a@example.com"


def test_starttls_with_login_roundtrip(nf):
    f = {"host": "mail.example.com", "security": "starttls", "sender": "tc@example.com", "to": "a@example.com; b@example.com",
         "user": "tc@example.com", "password": "p@ss:w/rd"}
    url = nf.email_url(f)
    assert url.startswith("mailtos://tc%40example.com:p%40ss%3Aw%2Frd@mail.example.com:587?") and url.endswith("&mode=starttls")
    pub = nf.email_parts(url)
    assert pub == {"host": "mail.example.com", "port": "587", "security": "starttls", "sender": "tc@example.com",
                   "to": "a@example.com, b@example.com", "user": "tc@example.com"}
    assert nf.email_parts(url, secret=True)["password"] == "p@ss:w/rd"
    assert nf.email_url(nf.email_parts(url, secret=True)) == url  # editing without a new password keeps it


@pytest.mark.parametrize("f,msg", [
    ({"host": "smtp x", "sender": "a@b.c", "to": "a@b.c"}, "server"),
    ({"host": "h", "sender": "nope", "to": "a@b.c"}, "from"),
    ({"host": "h", "sender": "a@b.c", "to": ""}, "to"),
    ({"host": "h", "sender": "a@b.c", "to": "a@b.c", "port": "70000"}, "port"),
])
def test_rejects(nf, f, msg):
    with pytest.raises(ValueError, match=msg):
        nf.email_url(f)


def test_apprise_understands_it(nf):
    apprise = pytest.importorskip("apprise")
    a = apprise.Apprise()
    assert a.add(nf.email_url({"host": "mail.example.com", "security": "ssl", "sender": "tc@example.com", "to": "a@example.com",
                               "user": "u", "password": "p"}))
    assert a[0].smtp_host == "mail.example.com" and a[0].port == 465 and a[0].secure_mode == "ssl"


def test_page_shows_email_fields_without_password(client, nf, monkeypatch):
    url = "mailtos://u:topsecret@mail.example.com:587?from=tc@example.com&to=a@example.com&mode=starttls"
    monkeypatch.setattr(nf, "load", lambda: {"channels": [{"id": "mail", "name": "mail", "kind": "email", "enabled": True}],
                                             "routes": {}, "quiet": {"enabled": False, "from": "23:00", "to": "07:00"}, "dedupe_min": 10})
    monkeypatch.setattr(nf, "channel_url", lambda cid: url)
    r = client.get("/notifications")
    assert r.status_code == 200 and "mail.example.com" in r.text and "a@example.com" in r.text and "topsecret" not in r.text
