"""Module packages (control/app/packages.py): the ZIP format, signatures, the checks against crafted ZIPs, install,
the check at start, rollback and remove – with throwaway keys."""
import base64
import hashlib
import io
import json
import os
import stat
import warnings
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def b64e(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def new_key():
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return priv, b64e(pub)


def files_of(name="tcx_pkg", version="1.0.0"):
    return {f"{name}/__init__.py": b"def register(api):\n    pass\n",
            f"{name}/extension.json": json.dumps({"name": name, "title": "Package test", "version": version,
                                                  "requires_api": 1}).encode(),
            f"{name}/templates/page.html": b"<p>hi</p>"}


def build(name="tcx_pkg", version="1.0.0", files=None, priv=None, manifest=None, extra=(), sig=None):
    """a package ZIP: files (path → bytes), signed with priv; manifest overrides fields of tcpkg.json;
    extra = (ZipInfo or name, bytes) added after the listed files; sig replaces tcpkg.sig"""
    files = files_of(name, version) if files is None else files
    m = {"format": 1, "name": name, "version": version, "requires_api": 1, "title": "Package test",
         "files": {p: hashlib.sha256(b).hexdigest() for p, b in files.items()}}
    m.update(manifest or {})
    raw = json.dumps(m, indent=1).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z, warnings.catch_warnings():
        warnings.simplefilter("ignore")  # a duplicate name is one of the tests
        z.writestr("tcpkg.json", raw)
        if sig is not None:
            z.writestr("tcpkg.sig", sig)
        elif priv:
            z.writestr("tcpkg.sig", json.dumps({"key": "", "sig": b64e(priv.sign(raw))}))
        for p, b in files.items():
            z.writestr(p, b)
        for info, b in extra:
            z.writestr(info, b)
    return buf.getvalue()


@pytest.fixture
def pk(app_module, tmp_path, monkeypatch):
    """packages with PACKAGES_DIR in tmp_path and one trusted throwaway key; returns (module, private key)"""
    from app import packages
    priv, pub = new_key()
    monkeypatch.setattr(packages, "PACKAGES_DIR", str(tmp_path / "packages"))
    monkeypatch.setattr(packages, "PREV_DIR", str(tmp_path / "packages" / ".prev"))
    monkeypatch.setattr(packages, "PACKAGE_KEYS", [pub])
    return packages, priv


def test_signed_and_unsigned_packages(pk):
    packages, priv = pk
    s = packages.inspect(build(priv=priv))
    assert s["signed"] and s["key"] == packages.fingerprint(packages.PACKAGE_KEYS[0]) and s["version"] == "1.0.0"
    assert s["files"] == 3 and s["installed"] == ""
    u = packages.inspect(build())
    assert not u["signed"] and u["key"] == ""
    other, _ = new_key()
    with pytest.raises(packages.PackageError, match="doesn't match"):  # a key nobody named, not ours
        packages.inspect(build(priv=other))
    raw = json.dumps({"key": "AAAA BBBB CCCC DDDD", "sig": b64e(other.sign(b"x"))})
    with pytest.raises(packages.PackageError, match="doesn't know"):
        packages.inspect(build(sig=raw))
    with pytest.raises(packages.PackageError, match="damaged"):
        packages.inspect(build(sig="not json"))


def test_tampering_breaks_the_signature(pk):
    packages, priv = pk
    data = build(priv=priv)
    # change one byte of tcpkg.json inside the ZIP: the signature no longer matches
    zin = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in zin.infolist():
            b = zin.read(i)
            z.writestr(i.filename, b.replace(b'"1.0.0"', b'"9.0.0"') if i.filename == "tcpkg.json" else b)
    with pytest.raises(packages.PackageError, match="changed after signing"):
        packages.inspect(buf.getvalue())
    # swap a file but keep the signed tcpkg.json: its checksum catches it
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in zin.infolist():
            b = zin.read(i)
            z.writestr(i.filename, b"import os  # evil\n" if i.filename == "tcx_pkg/__init__.py" else b)
    with pytest.raises(packages.PackageError, match="__init__.py: checksum"):
        packages.inspect(buf.getvalue())


@pytest.mark.parametrize("kw, msg", [
    ({"manifest": {"format": 2}}, "format"),
    ({"manifest": {"name": "Bad-Name"}}, "name"),
    ({"manifest": {"version": "1 0"}}, "version"),
    ({"manifest": {"requires_api": 99}}, "Extension API"),
    ({"manifest": {"files": {}}}, "no files"),
    ({"extra": [("tcx_pkg/sneaky.py", b"x")]}, "not in tcpkg.json"),
    ({"extra": [("tcx_pkg/__init__.py", b"again")]}, "twice"),
    ({"files": {"tcx_pkg/__init__.py": b"", "../evil.py": b"x"}}, "belongs in the folder"),
    ({"files": {"tcx_pkg/__init__.py": b"", "tcx_pkg/../../evil.py": b"x"}}, "plain relative path"),
    ({"files": {"tcx_pkg/__init__.py": b"", "/etc/evil.py": b"x"}}, "belongs in the folder"),
    ({"files": {"tcx_pkg/__init__.py": b"", "tcx_pkg/lib.so": b"x"}}, "file type"),
    ({"files": {"tcx_pkg/__init__.py": b"", "tcx_pkg/__pycache__/x.py": b"x"}}, "plain relative path"),
    ({"files": {"tcx_pkg/extension.json": b"{}", "tcx_pkg/x.py": b""}}, "__init__.py"),
    ({"files": {"tcx_pkg/__init__.py": b""}}, "extension.json"),
    ({"files": {"tcx_pkg/__init__.py": b"", "tcx_pkg/extension.json": b'{"name": "other", "requires_api": 1}'}}, "names 'other'"),
])
def test_refused_packages(pk, kw, msg):
    packages, priv = pk
    with pytest.raises(packages.PackageError, match=msg):
        packages.inspect(build(priv=priv, **kw))


def test_refused_zip_files(pk):
    packages, priv = pk
    with pytest.raises(packages.PackageError, match="not a ZIP"):
        packages.inspect(b"PK not really")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("tcx_pkg/__init__.py", b"")
    with pytest.raises(packages.PackageError, match="no tcpkg.json"):
        packages.inspect(buf.getvalue())
    link = zipfile.ZipInfo("tcx_pkg/link.py")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    files = files_of()
    files["tcx_pkg/link.py"] = b"/etc/passwd"
    data = build(priv=priv, files={k: v for k, v in files.items() if k != "tcx_pkg/link.py"},
                 manifest={"files": {p: hashlib.sha256(b).hexdigest() for p, b in files.items()}},
                 extra=[(link, b"/etc/passwd")])
    with pytest.raises(packages.PackageError, match="symlink"):
        packages.inspect(data)


def test_size_limits(pk, monkeypatch):
    packages, priv = pk
    monkeypatch.setattr(packages, "MAX_UNPACKED", 100)
    with pytest.raises(packages.PackageError, match="unpacked"):  # a small ZIP that unpacks big
        packages.inspect(build(priv=priv, files=dict(files_of(), **{"tcx_pkg/big.txt": b"0" * 5000})))
    monkeypatch.setattr(packages, "MAX_ZIP", 10)
    with pytest.raises(packages.PackageError, match="larger than"):
        packages.inspect(build(priv=priv))


def test_install_verify_update_rollback_remove(pk):
    packages, priv = pk
    with pytest.raises(packages.PackageError, match="not signed"):
        packages.install(build())
    s = packages.install(build(priv=priv))
    assert s["signed"] and s["name"] == "tcx_pkg"
    base = os.path.join(packages.PACKAGES_DIR, "tcx_pkg")
    assert os.path.isfile(os.path.join(base, "tcx_pkg", "__init__.py")) and os.path.isfile(os.path.join(base, "tcpkg.sig"))
    v = packages.verify_installed("tcx_pkg")
    assert v["signed"] and not v["error"] and v["path"] == os.path.join(base, "tcx_pkg")
    assert packages.inspect(build(priv=priv, version="1.1.0"))["installed"] == "1.0.0"

    os.makedirs(os.path.join(base, "tcx_pkg", "__pycache__"))  # compiled caches are removed, not counted
    open(os.path.join(base, "tcx_pkg", "__pycache__", "x.pyc"), "wb").close()
    assert not packages.verify_installed("tcx_pkg")["error"]
    assert not os.path.exists(os.path.join(base, "tcx_pkg", "__pycache__"))

    packages.install(build(priv=priv, version="1.1.0"))
    inst = packages.installed()["tcx_pkg"]
    assert inst["version"] == "1.1.0" and inst["previous"] == "1.0.0" and inst["signed"]
    assert packages.rollback("tcx_pkg") == "1.0.0"
    assert packages.installed()["tcx_pkg"]["previous"] == "1.1.0"
    packages.remove("tcx_pkg")
    assert packages.installed() == {}
    with pytest.raises(packages.PackageError, match="no earlier version"):
        packages.rollback("tcx_pkg")
    with pytest.raises(packages.PackageError, match="not a package name"):
        packages.remove("../x")


def test_unsigned_install_and_changes_after_installing(pk):
    packages, priv = pk
    assert not packages.install(build(), allow_unsigned=True)["signed"]
    assert not packages.verify_installed("tcx_pkg")["error"]
    packages.install(build(priv=priv))
    pkg = os.path.join(packages.PACKAGES_DIR, "tcx_pkg", "tcx_pkg")
    with open(os.path.join(pkg, "__init__.py"), "a") as f:
        f.write("# changed\n")
    v = packages.verify_installed("tcx_pkg")
    assert "__init__.py: changed after installing" in v["error"] and not v["signed"]
    packages.install(build(priv=priv))
    open(os.path.join(pkg, "added.py"), "w").close()
    assert "added.py: added or removed" in packages.verify_installed("tcx_pkg")["error"]
    packages.install(build(priv=priv))
    with open(os.path.join(packages.PACKAGES_DIR, "tcx_pkg", "tcpkg.json"), "a") as f:
        f.write(" ")
    assert "changed after signing" in packages.verify_installed("tcx_pkg")["error"]


def test_key_removed_from_the_trusted_list(pk, monkeypatch):
    packages, priv = pk
    packages.install(build(priv=priv))
    monkeypatch.setattr(packages, "PACKAGE_KEYS", [])
    v = packages.verify_installed("tcx_pkg")
    assert v["error"] and not v["signed"]
