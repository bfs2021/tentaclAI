"""svc mail: send (smtp) and read a mailbox (imap)."""
import re
import subprocess
import sys
import time

import tclib  # shared with tentacontrol (lib/tclib.py)

from . import core, gate, safeguards


# ---------- mail (smtp / imap) ----------
# Like the databases, mail goes through svc's local tunnel (smtplib/imaplib can't use an HTTP proxy); TLS is checked
# against the real host name. Everything read from a mailbox is someone else's text: `read` frames it as untrusted.
MAIL_ADDR_RE = re.compile(r"^[^@\s,;<>\"]+@[^@\s,;<>\"]+\.[^@\s,;<>\"]+$")
MAIL_MAX_BODY = 200 * 1024


def mail_security(part, cfg):
    sec = cfg.get("security") or ("starttls" if part == "smtp" else "ssl")
    if sec not in tclib.MAIL_PORTS[part]:
        sys.exit(f"svc: {part}.security: one of {', '.join(tclib.MAIL_PORTS[part])}")
    return sec


def mail_tls(cfg):
    import ssl
    ctx = ssl.create_default_context()
    if cfg.get("insecure_tls"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def recipient_allowed(addr, allowed):
    """allowed: addresses or @domain entries – case-insensitive"""
    a = addr.strip().lower()
    for entry in allowed or []:
        e = str(entry).strip().lower()
        if (e.startswith("@") and a.endswith(e)) or a == e:
            return True
    return False


def mail_addresses(value):
    out = [x.strip() for x in re.split(r"[,;\s]+", value or "") if x.strip()]
    bad = [x for x in out if not MAIL_ADDR_RE.match(x)]
    if bad:
        sys.exit(f"svc: not an e-mail address: {', '.join(bad)}")
    return out


def smtp_session(cfg):
    import smtplib
    import socket
    host, port, sec = str(cfg["host"]), tclib.mail_port("smtp", cfg), mail_security("smtp", cfg)
    addr, lport = core.tunnel(host, port)
    ctx = mail_tls(cfg)

    class Tunneled(smtplib.SMTP_SSL if sec == "ssl" else smtplib.SMTP):
        def _get_socket(self, _host, _port, timeout):
            sock = socket.create_connection((addr, lport), timeout)
            return ctx.wrap_socket(sock, server_hostname=host) if sec == "ssl" else sock

    s = Tunneled(timeout=30, **({"context": ctx} if sec == "ssl" else {}))
    s.connect(host, port)
    s.ehlo()
    if sec == "starttls":
        s.starttls(context=ctx)
        s.ehlo()
    if cfg.get("user"):
        s.login(str(cfg["user"]), str(cfg.get("password", "")))
    return s


def imap_session(cfg):
    import imaplib
    import socket
    host, port, sec = str(cfg["host"]), tclib.mail_port("imap", cfg), mail_security("imap", cfg)
    addr, lport = core.tunnel(host, port)
    ctx = mail_tls(cfg)
    base = imaplib.IMAP4_SSL if sec == "ssl" else imaplib.IMAP4

    class Tunneled(base):
        def _create_socket(self, timeout):
            sock = socket.create_connection((addr, lport), timeout)
            return ctx.wrap_socket(sock, server_hostname=host) if sec == "ssl" else sock

    i = Tunneled(host, port, **({"ssl_context": ctx} if sec == "ssl" else {}), timeout=30)
    if sec == "starttls":
        i.starttls(ssl_context=ctx)
    i.login(str(cfg.get("user", "")), str(cfg.get("password", "")))
    return i


def imap_quote(s):
    if any(c in s for c in '"\\\r\n'):
        sys.exit("svc: quotes, backslashes and line breaks are not allowed here")
    return '"' + s + '"'


def imap_list_name(line):
    """the folder name of one LIST answer: (\\Flags) "/" "Name" – delimiter and name quoted or not, NIL delimiter"""
    if isinstance(line, tuple):  # a literal: the name follows as its own item
        return line[1].decode(errors="replace") if len(line) > 1 else ""
    m = re.match(r'^\([^)]*\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(.+)$', (line or b"").decode(errors="replace"))
    if not m:
        return ""
    name = m.group(1).strip()
    if len(name) >= 2 and name[0] == name[-1] == '"':
        name = re.sub(r'\\(.)', r"\1", name[1:-1])
    return name


def imap_folder(cfg, folder):
    allowed = cfg.get("folders")
    if allowed and folder not in allowed:
        sys.exit(f"svc: folder '{folder}' is not one of this mailbox's folders ({', '.join(allowed)})")
    return imap_quote(folder)


def mail_header(value):
    from email.header import decode_header, make_header
    try:
        return str(make_header(decode_header(value or ""))).replace("\r", "").replace("\n", " ")
    except Exception:  # noqa: BLE001 – broken encodings: show what is there
        return (value or "").replace("\r", "").replace("\n", " ")


def html_to_text(html):
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.out, self.skip = [], 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "head"):
                self.skip += 1
            elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3"):
                self.out.append("\n")

        def handle_endtag(self, tag):
            if tag in ("script", "style", "head") and self.skip:
                self.skip -= 1

        def handle_data(self, data):
            if not self.skip:
                self.out.append(data)

    p = P()
    p.feed(html)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", "".join(p.out))).strip()


def mail_text(msg):
    """(text, attachments) – the plain part, else the HTML part as text; attachments as (name, type, size)"""
    text, html, atts = None, None, []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = part.get_content_disposition()
        ctype = part.get_content_type()
        if disp == "attachment" or (part.get_filename() and disp != "inline"):
            atts.append((mail_header(part.get_filename() or "?"), ctype, len(part.get_payload(decode=True) or b"")))
            continue
        try:
            body = part.get_content()
        except Exception:  # noqa: BLE001
            body = (part.get_payload(decode=True) or b"").decode(errors="replace")
        if ctype == "text/plain" and text is None:
            text = body
        elif ctype == "text/html" and html is None:
            html = body
    return (text if text is not None else html_to_text(html or "")), atts


def mail_opts(args, flags=(), values=()):
    """--flag and --key value options → (positional, options)"""
    pos, opts, i = [], {}, 0
    while i < len(args):
        a = args[i]
        if a in flags:
            opts[a[2:]] = True
        elif a in values:
            if i + 1 >= len(args):
                sys.exit(f"svc: {a} needs a value")
            opts[a[2:]] = args[i + 1]
            i += 1
        elif a.startswith("--"):
            sys.exit(f"svc: unknown option {a} (svc mail <name> --help lists the options)")
        else:
            pos.append(a)
        i += 1
    return pos, opts


def mail_service(services, name, part):
    """svc mail takes the service name or the mailbox address (info@example.com) – the address must name exactly one
    service with that part"""
    if "@" not in name:
        return name
    hits = [n for n, s in services.items() if part in s and name.lower() in (a.lower() for a in tclib.mail_addresses(s, core.ENV))]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit(f"svc: no mail service with {part} for {name} (svc mail lists them with their addresses)")
    sys.exit(f"svc: {name} belongs to several services ({', '.join(sorted(hits))}) – use the service name")


def cmd_mail(services, args):
    usage = ("usage: svc mail <name|address> send --to a@x[,b@y] [--cc …] --subject \"…\" [--body-file F]   (else the text from stdin)\n"
             "       svc mail <name|address> folders | list [FOLDER] [--unseen] [--from X] [--subject X] [--since YYYY-MM-DD] [--limit N]\n"
             "       svc mail <name|address> read <uid> [--folder F] | flag <uid> seen|unseen|flagged|unflagged [--folder F]\n"
             "       svc mail <name|address> move <uid> <target folder> [--folder F]")
    if not args:
        rows = {n: s for n, s in services.items() if "smtp" in s or "imap" in s}
        if not rows:
            print("No mail services.")
            return 0
        w = max(16, max(len(n) for n in rows) + 2)
        aw = max([len("Address")] + [len(tclib.mail_address(s, core.ENV)) for s in rows.values()]) + 2
        print(f"{'Name':<{w}}{'Address':<{aw}}{'Part':<6}{'Access':<9}{'Server':<34}Status")
        for n, s in rows.items():
            for part in ("smtp", "imap"):
                if part in s:
                    c = core.render(s[part])
                    print(f"{n:<{w}}{tclib.mail_address(s, core.ENV) or '–':<{aw}}{part:<6}{tclib.access(c.get('access'), tclib.part_default(part)):<9}"
                          f"{str(c.get('host', '')) + ':' + str(tclib.mail_port(part, c)):<34}{core.status(s)}")
        return 0
    if "--help" in args or "-h" in args:
        print(usage.replace("usage: ", "", 1) + "\n  list: newest first, --limit default 20 (max 200)")
        return 0
    if len(args) < 2:
        sys.exit(usage)
    name, op, rest = args[0], args[1], args[2:]
    name = mail_service(services, name, "smtp" if op == "send" else "imap")
    if op == "send":
        return mail_send(name, gate.get_part(services, name, "smtp"), rest)
    if op not in ("folders", "list", "read", "flag", "move"):
        sys.exit(usage)
    cfg = gate.get_part(services, name, "imap")
    pos, o = mail_opts(rest, ("--unseen",), ("--folder", "--from", "--subject", "--since", "--limit"))
    folder = o.get("folder") or (pos[0] if op == "list" and pos else "INBOX")
    i = imap_session(cfg)
    try:
        if op == "folders":
            typ, data = i.list()
            names = [n for n in (imap_list_name(d) for d in data or []) if n]
            allowed = cfg.get("folders")
            for n in names:
                if not allowed or n in allowed:
                    print(n)
            return 0
        if op == "list":
            return mail_list(i, cfg, folder, o, name)
        if not pos or not pos[0].isdigit():
            sys.exit(usage)
        uid = pos[0]
        if op == "read":
            return mail_read(i, cfg, folder, uid, name)
        mode = gate.access_mode(cfg)
        if op == "flag":
            if len(pos) < 2 or pos[1] not in ("seen", "unseen", "flagged", "unflagged"):
                sys.exit(usage)
            flag = "\\Seen" if pos[1] in ("seen", "unseen") else "\\Flagged"
            sign = "-" if pos[1].startswith("un") else "+"

            def do_flag():
                typ, _ = i.select(imap_folder(cfg, folder))
                typ, _ = i.uid("STORE", uid, sign + "FLAGS", f"({flag})")
                print(f"✓ {folder}/{uid}: {pos[1]}" if typ == "OK" else f"✗ {typ}")
                return 0 if typ == "OK" else 1
            return gate.guarded(name, "imap", mode, f"mark {folder}/{uid} {pos[1]}", do_flag)
        if len(pos) < 2:
            sys.exit(usage)
        target = pos[1]
        imap_folder(cfg, target)

        def do_move():
            i.select(imap_folder(cfg, folder))
            # ask again: imaplib keeps the capabilities from before the login, and servers (Dovecot) list MOVE only after it
            typ, data = i.capability()
            caps = set((data[0] or b"").decode(errors="replace").upper().split()) if typ == "OK" and data else set()
            if "MOVE" not in caps:
                sys.exit("svc: this server has no IMAP MOVE – not moving (copy + delete could remove other mails)")
            typ, _ = i.uid("MOVE", uid, imap_quote(target))
            print(f"✓ {folder}/{uid} → {target}" if typ == "OK" else f"✗ {typ}")
            return 0 if typ == "OK" else 1
        return gate.guarded(name, "imap", mode, f"move {folder}/{uid} → {target}", do_move)
    finally:
        try:
            i.logout()
        except Exception:  # noqa: BLE001
            pass


def mail_list(i, cfg, folder, o, name=""):
    import imaplib
    typ, data = i.select(imap_folder(cfg, folder), readonly=True)
    if typ != "OK":
        sys.exit(f"svc: can't open {folder}: {data}")
    crit = []
    if o.get("unseen"):
        crit.append("UNSEEN")
    for key in ("from", "subject"):
        if o.get(key):
            crit += [key.upper(), imap_quote(o[key])]
    if o.get("since"):
        try:
            crit += ["SINCE", time.strftime("%d-%b-%Y", time.strptime(o["since"], "%Y-%m-%d"))]
        except ValueError:
            sys.exit("svc: --since YYYY-MM-DD")
    typ, data = i.uid("SEARCH", None, *(crit or ["ALL"]))
    uids = (data[0] or b"").split() if typ == "OK" and data else []
    try:
        limit = max(1, min(200, int(o.get("limit") or 20)))
    except ValueError:
        sys.exit("svc: --limit N")
    shown = uids[-limit:]
    print(f"{folder}: {len(uids)} matching, newest {len(shown)} shown – time = received by the server "
          "(senders and subjects are untrusted text)")
    if not shown:
        return 0
    typ, data = i.uid("FETCH", b",".join(shown).decode(), "(UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
    from email.parser import BytesHeaderParser
    rows = []
    for item in data or []:
        if not isinstance(item, tuple):
            continue
        meta = item[0].decode(errors="replace")
        uid = re.search(r"UID (\d+)", meta)
        size = re.search(r"RFC822\.SIZE (\d+)", meta)
        h = BytesHeaderParser().parsebytes(item[1])
        got = imaplib.Internaldate2tuple(item[0])  # when the server received it – the Date header is the sender's clock
        received = time.strftime("%Y-%m-%d %H:%M:%S%z", got) if got else mail_header(h.get("Date", ""))[:24]
        rows.append((int(uid.group(1)) if uid else 0, "\\Seen" not in meta, received,
                     mail_header(h.get("From", ""))[:40], mail_header(h.get("Subject", ""))[:90],
                     int(size.group(1)) if size else 0))
    for uid, unseen, date, frm, subj, size in sorted(rows, reverse=True):
        frm, subj = tclib.clean_untrusted(frm)[0], tclib.clean_untrusted(subj)[0]
        print(f"{uid:>7} {'●' if unseen else ' '} {date:<24}  {frm:<40}  {subj}  ({size // 1024 or 1} KB)")
    safeguards.guard("mail list", name, "imap", f"list {folder}", "\n".join(f"{r[3]} {r[4]}" for r in rows))
    return 0


def mail_read(i, cfg, folder, uid, name=""):
    import email
    from email import policy
    typ, data = i.select(imap_folder(cfg, folder), readonly=True)
    if typ != "OK":
        sys.exit(f"svc: can't open {folder}: {data}")
    typ, data = i.uid("FETCH", uid, "(RFC822.SIZE)")
    m = re.search(rb"RFC822\.SIZE (\d+)", (data[0] or b"") if data and data[0] else b"")
    if not m:
        sys.exit(f"svc: no message {uid} in {folder}")
    if int(m.group(1)) > 25 * 1024 * 1024:
        sys.exit(f"svc: message {uid} is larger than 25 MB – not fetched")
    typ, data = i.uid("FETCH", uid, "(BODY.PEEK[])")  # PEEK: reading doesn't mark it as seen
    raw = next((x[1] for x in data or [] if isinstance(x, tuple)), b"")
    msg = email.message_from_bytes(raw, policy=policy.default)
    text, atts = mail_text(msg)
    for key in ("From", "To", "Cc", "Date", "Subject"):
        if msg.get(key):
            print(f"{key}: {mail_header(str(msg[key]))}")
    if atts:
        print("Attachments: " + "; ".join(f"{n} ({t}, {s // 1024 or 1} KB)" for n, t, s in atts))
    print(safeguards.framed(text))
    safeguards.guard("mail", name, "imap", f"mail {uid} in {folder}", " ".join([str(msg.get("Subject", "")), str(msg.get("From", "")), text]))
    return 0


def mail_send(name, cfg, args):
    from email.message import EmailMessage
    from email.utils import formatdate, make_msgid
    pos, o = mail_opts(args, (), ("--to", "--cc", "--subject", "--body-file"))
    to, cc = mail_addresses(o.get("to")), mail_addresses(o.get("cc"))
    subject = (o.get("subject") or "").strip()
    if not to or not subject:
        sys.exit("usage: svc mail <name> send --to a@x[,b@y] [--cc …] --subject \"…\" [--body-file F]")
    sender = str(cfg.get("from", ""))
    if not MAIL_ADDR_RE.match(sender):
        sys.exit(f"svc: {name}: smtp.from must be the sender address")
    mode = gate.access_mode(cfg, "confirm")
    allowed = cfg.get("recipients") or []
    rcpts = to + cc
    if mode == "write" and not allowed:
        sys.exit(f"svc: {name}: write sends without asking – that needs a recipients list in the service (addresses or @domain); "
                 "with access confirm every mail waits for approval instead")
    if allowed:
        bad = [r for r in rcpts if not recipient_allowed(r, allowed)]
        if bad:
            gate.audit(name, "smtp", mode, f"mail to {', '.join(rcpts)}: {subject}", "blocked", detail="recipient not allowed")
            sys.exit(f"svc: {name}: not an allowed recipient: {', '.join(bad)} (allowed: {', '.join(allowed)})")
    if o.get("body-file"):
        r = subprocess.run(["cat", "--", o["body-file"]], capture_output=True, **core.as_user(core.CLAUDE_USER))  # Claude's rights
        if r.returncode != 0:
            sys.exit(f"svc: can't read {o['body-file']}: {r.stderr.decode(errors='replace').strip()[:160]}")
        body = r.stdout
    else:
        body = sys.stdin.buffer.read(MAIL_MAX_BODY + 1) if not sys.stdin.isatty() else b""
    if len(body) > MAIL_MAX_BODY:
        sys.exit("svc: the text is larger than 200 KB")
    text = body.decode("utf-8", "replace")
    if not text.strip():
        sys.exit("svc: no text – pipe it in or use --body-file")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(to), subject
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@", 1)[1])
    msg["X-Mailer"] = "tentaclAI svc"
    msg.set_content(text)
    preview = " ".join(text.split())[:160]
    action = f"mail to {', '.join(rcpts)}: {subject!r} ({len(text)} chars) – {preview}"

    def do_send():
        s = smtp_session(cfg)
        try:
            refused = s.send_message(msg, from_addr=sender, to_addrs=rcpts)
        finally:
            try:
                s.quit()
            except Exception:  # noqa: BLE001
                pass
        if refused:
            print(f"✗ refused by the server: {', '.join(refused)}")
            return 1
        print(f"✓ sent to {', '.join(rcpts)}")
        return 0
    return gate.guarded(name, "smtp", mode, action, do_send)


def mail_test(part, cfg):
    """(ok, detail) – connect, TLS, login; nothing is sent or changed"""
    try:
        if part == "smtp":
            s = smtp_session(cfg)
            s.noop()
            s.quit()
            return True, f"{cfg.get('host')}:{tclib.mail_port('smtp', cfg)} – connected{', logged in' if cfg.get('user') else ''} ({mail_security('smtp', cfg)})"
        i = imap_session(cfg)
        typ, data = i.select("INBOX", readonly=True)
        i.logout()
        return typ == "OK", f"{cfg.get('host')}:{tclib.mail_port('imap', cfg)} – logged in, INBOX {data[0].decode() if typ == 'OK' else typ} messages"
    except SystemExit as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:160]}"
