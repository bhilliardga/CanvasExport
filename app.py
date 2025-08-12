import io, os, shutil, tempfile, zipfile, json, time
from typing import List, Dict, Any, Optional, Tuple, Set
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import requests
from html.parser import HTMLParser
import re
import os
import requests
from typing import Set

app = FastAPI(title="Canvas Exporter")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("ALLOWED_ORIGIN", "*")],  # set to your domain in prod
    allow_credentials=False,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)

# ---------------- HTTP helpers & pagination ----------------
def _next_link(headers: Dict[str, str]) -> Optional[str]:
    link = headers.get("Link") or headers.get("link")
    if not link: return None
    for part in link.split(","):
        segs = [s.strip() for s in part.split(";")]
        if len(segs) < 2: continue
        if any('rel="next"' in s for s in segs[1:]):
            return segs[0].strip()[1:-1]
    return None

def _get(url: str, headers: Dict[str, str], params: Dict[str, Any] | None = None) -> requests.Response:
    tries = 0
    while True:
        r = requests.get(url, headers=headers, params=params, timeout=60)
        if r.status_code == 403 and "Rate Limit" in (r.text or "") and tries < 4:
            tries += 1
            time.sleep(min(30, 2 ** tries))
            continue
        r.raise_for_status()
        return r

def fetch_all(url: str, headers: Dict[str, str], params: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    out, first = [], True
    while url:
        r = _get(url, headers, params if first else None)
        first = False
        data = r.json()
        out.extend(data if isinstance(data, list) else [data])
        url = _next_link(r.headers)
    return out

# ---------------- Canvas endpoints ----------------
def get_courses(api_base: str, token: str, include_concluded=False) -> List[Dict[str, Any]]:
    h = {"Authorization": f"Bearer {token}"}
    p = {"per_page": 100}
    if not include_concluded: p["enrollment_state"] = "active"
    return fetch_all(f"{api_base}/users/self/courses", h, p)

def get_course_detail(api_base: str, token: str, cid: int) -> Dict[str, Any]:
    h = {"Authorization": f"Bearer {token}"}
    return _get(f"{api_base}/courses/{cid}", h).json()

def get_assignments(api_base: str, token: str, cid: int) -> List[Dict[str, Any]]:
    h = {"Authorization": f"Bearer {token}"}
    return fetch_all(f"{api_base}/courses/{cid}/assignments", h, {"per_page": 100})

def get_pages(api_base: str, token: str, cid: int) -> List[Dict[str, Any]]:
    h = {"Authorization": f"Bearer {token}"}
    return fetch_all(f"{api_base}/courses/{cid}/pages", h, {"per_page": 100, "include[]": "body"})

def get_page_by_url(api_base: str, token: str, cid: int, page_url: str) -> Dict[str, Any]:
    h = {"Authorization": f"Bearer {token}"}
    return _get(f"{api_base}/courses/{cid}/pages/{page_url}", h).json()

def get_modules(api_base: str, token: str, cid: int) -> List[Dict[str, Any]]:
    h = {"Authorization": f"Bearer {token}"}
    return fetch_all(f"{api_base}/courses/{cid}/modules", h, {"per_page": 100})

def get_module_items(api_base: str, token: str, cid: int, mid: int) -> List[Dict[str, Any]]:
    h = {"Authorization": f"Bearer {token}"}
    return fetch_all(f"{api_base}/courses/{cid}/modules/{mid}/items", h, {"per_page": 100, "include[]": "content_details"})

def get_files(api_base: str, token: str, cid: int) -> List[Dict[str, Any]]:
    h = {"Authorization": f"Bearer {token}"}
    return fetch_all(f"{api_base}/courses/{cid}/files", h, {"per_page": 100})

def get_file_by_id(api_base: str, token: str, file_id: int) -> Dict[str, Any]:
    h = {"Authorization": f"Bearer {token}"}
    return _get(f"{api_base}/files/{file_id}", h).json()

def get_discussions(api_base: str, token: str, cid: int) -> List[Dict[str, Any]]:
    h = {"Authorization": f"Bearer {token}"}
    return fetch_all(f"{api_base}/courses/{cid}/discussion_topics", h, {"per_page": 100})

def get_discussion(api_base: str, token: str, cid: int, tid: int) -> Dict[str, Any]:
    h = {"Authorization": f"Bearer {token}"}
    return _get(f"{api_base}/courses/{cid}/discussion_topics/{tid}", h).json()

def get_quizzes(api_base: str, token: str, cid: int) -> List[Dict[str, Any]]:
    h = {"Authorization": f"Bearer {token}"}
    return fetch_all(f"{api_base}/courses/{cid}/quizzes", h, {"per_page": 100})

def get_quiz(api_base: str, token: str, cid: int, qid: int) -> Dict[str, Any]:
    h = {"Authorization": f"Bearer {token}"}
    return _get(f"{api_base}/courses/{cid}/quizzes/{qid}", h).json()

# ---------------- Page-linked file extraction ----------------
class _FileLinkHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: List[str] = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a": return
        m = {k.lower(): (v or "") for k, v in attrs}
        cls = m.get("class", "")
        if not cls: return
        tokens = {c.strip().lower() for c in re.split(r"\s+", cls)}
        if ("instructure_file_link" in tokens) or ("instructure_scribd_file" in tokens):
            href = m.get("href", "")
            if href: self.hrefs.append(href)

_FILE_ID_FROM_HREF = re.compile(r"/files/(\d+)\b")

def extract_file_refs_from_html(html: str) -> List[Tuple[int, str]]:
    refs: List[Tuple[int, str]] = []
    if not html: return refs
    p = _FileLinkHTMLParser(); p.feed(html)
    for href in p.hrefs:
        m = _FILE_ID_FROM_HREF.search(href)
        if m:
            try: refs.append((int(m.group(1)), href))
            except ValueError: pass
    if not refs:  # fallback
        for fid in re.findall(r"/(?:courses/\d+/)?files/(\d+)", html):
            try: refs.append((int(fid), ""))
            except ValueError: pass
    seen, out = set(), []
    for fid, href in refs:
        if fid not in seen:
            seen.add(fid); out.append((fid, href))
    return out

def download_file_to(path: str, url: str, headers: Dict[str, str]) -> None:
    with requests.get(url, stream=True, timeout=60) as r:
        if r.status_code in (401, 403):
            r.close()
            with requests.get(url, headers=headers, stream=True, timeout=60) as rr:
                rr.raise_for_status(); _stream_save(rr, path)
        else:
            r.raise_for_status(); _stream_save(r, path)

def _stream_save(resp: requests.Response, path: str) -> None:
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        for chunk in resp.iter_content(262144):
            if chunk: f.write(chunk)
    os.replace(tmp, path)

# ---------------- Enrichment ----------------
def enrich_module_items(api_base, token, cid, items, pages_index, assignments_index, files_index, discussions_index, quizzes_index):
    for it in items:
        t = it.get("type")
        if t == "Page":
            page_url = it.get("page_url") or (it.get("content_details") or {}).get("page_url") or (it.get("url") or "").rsplit("/", 1)[-1]
            if page_url:
                page = pages_index.get(page_url) or get_page_by_url(api_base, token, cid, page_url)
                it["page"] = page
        elif t == "Assignment":
            aid = it.get("content_id"); 
            if isinstance(aid, int) and aid in assignments_index: it["assignment"] = assignments_index[aid]
        elif t == "File":
            fid = it.get("content_id")
            if isinstance(fid, int):
                meta = files_index.get(fid) or get_file_by_id(api_base, token, fid)
                files_index[fid] = meta; it["file"] = meta
        elif t == "Discussion":
            tid = it.get("content_id")
            if isinstance(tid, int):
                topic = discussions_index.get(tid) or get_discussion(api_base, token, cid, tid)
                discussions_index[tid] = topic; it["discussion"] = topic
        elif t == "Quiz":
            qid = it.get("content_id")
            if isinstance(qid, int):
                quiz = quizzes_index.get(qid) or get_quiz(api_base, token, cid, qid)
                quizzes_index[qid] = quiz; it["quiz"] = quiz

# ---------------- Course aggregation ----------------
def collect_course(api_base: str, token: str, c: Dict[str, Any]) -> Dict[str, Any]:
    cid = int(c["id"])
    out: Dict[str, Any] = {"id": cid, "name": c.get("name"), "course_code": c.get("course_code")}
    try: out["detail"] = get_course_detail(api_base, token, cid)
    except Exception as e: out["detail_error"] = str(e)

    assignments = []
    try: assignments = get_assignments(api_base, token, cid); out["assignments"] = assignments
    except Exception as e: out["assignments"] = []; out["assignments_error"] = str(e)
    assignments_index = {a.get("id"): a for a in assignments if a.get("id") is not None}

    pages = []
    try: pages = get_pages(api_base, token, cid); out["pages"] = pages
    except Exception as e: out["pages"] = []; out["pages_error"] = str(e)
    pages_index = {p.get("url", ""): p for p in pages if p.get("url")}

    files_list = []
    try: files_list = get_files(api_base, token, cid); out["files"] = files_list
    except Exception as e: out["files"] = []; out["files_error"] = str(e)
    files_index = {f.get("id"): f for f in files_list if f.get("id") is not None}

    discussions = []
    try: discussions = get_discussions(api_base, token, cid); out["discussions"] = discussions
    except Exception as e: out["discussions"] = []; out["discussions_error"] = str(e)
    discussions_index = {d.get("id"): d for d in discussions if d.get("id") is not None}

    quizzes = []
    try: quizzes = get_quizzes(api_base, token, cid); out["quizzes"] = quizzes
    except Exception as e: out["quizzes"] = []; out["quizzes_error"] = str(e)
    quizzes_index = {q.get("id"): q for q in quizzes if q.get("id") is not None}

    try:
        modules = get_modules(api_base, token, cid)
        for m in modules:
            mid = m.get("id")
            try:
                items = get_module_items(api_base, token, cid, int(mid))
            except Exception as e:
                m["items"] = []; m["_items_error"] = str(e); continue
            enrich_module_items(api_base, token, cid, items, pages_index, assignments_index, files_index, discussions_index, quizzes_index)
            m["items"] = items
        out["modules"] = modules
    except Exception as e:
        out["modules"] = []; out["modules_error"] = str(e)

    return out

def write_course_json(folder: str, course_obj: Dict[str, Any]) -> str:
    os.makedirs(folder, exist_ok=True)
    cid = course_obj.get("id")
    cname = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", (course_obj.get("name") or f"course_{cid}"))
    cname = re.sub(r"\s+", "_", cname)[:120]
    path = os.path.join(folder, f"{cid}_{cname}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(course_obj, f, indent=2, ensure_ascii=False)
    return path

def download_page_linked_files_for_course(api_base, token, course_obj, download_dir) -> Tuple[int, List[str]]:
    headers = {"Authorization": f"Bearer {token}"}
    pages = course_obj.get("pages") or []
    if not pages: return 0, []
    refs = []
    for p in pages:
        refs.extend(extract_file_refs_from_html(p.get("body") or ""))
    # dedupe
    seen, refs_dedup = set(), []
    for fid, href in refs:
        if fid not in seen:
            seen.add(fid); refs_dedup.append((fid, href))
    if not refs_dedup: return 0, []
    os.makedirs(download_dir, exist_ok=True)
    downloaded = []
    for fid, _ in refs_dedup:
        try:
            meta = get_file_by_id(api_base, token, fid)
            url = meta.get("url") or meta.get("download_url") or meta.get("public_url")
            if not url: continue
            name = meta.get("display_name") or meta.get("filename") or f"file_{fid}"
            out_path = os.path.join(download_dir, re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name))
            if os.path.exists(out_path): continue
            download_file_to(out_path, url, headers)
            downloaded.append(out_path)
        except Exception:
            pass
    return len(downloaded), downloaded

def download_all_course_files(api_base, token, course_obj, download_dir) -> Tuple[int, List[str]]:
    headers = {"Authorization": f"Bearer {token}"}
    files = course_obj.get("files") or []
    if not files: return 0, []
    os.makedirs(download_dir, exist_ok=True)
    downloaded = []
    for meta in files:
        try:
            fid = meta.get("id")
            name = meta.get("display_name") or meta.get("filename") or f"file_{fid}"
            out_path = os.path.join(download_dir, re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name))
            if os.path.exists(out_path): continue
            url = meta.get("url") or meta.get("download_url") or meta.get("public_url")
            if not url:
                meta = get_file_by_id(api_base, token, int(fid))
                url = meta.get("url") or meta.get("download_url") or meta.get("public_url")
                if not url: continue
            download_file_to(out_path, url, headers)
            downloaded.append(out_path)
        except Exception:
            pass
    return len(downloaded), downloaded

def make_zip_bytes(root_dir: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, _, files in os.walk(root_dir):
            for f in files:
                full = os.path.join(folder, f)
                rel = os.path.relpath(full, root_dir)
                z.write(full, rel)
    buf.seek(0)
    return buf.read()

# ---------------- API route ----------------
@app.post("/export")
def export_canvas(payload: Dict[str, Any]):
    """
    Body JSON:
    {
      "api_base": "https://myccsd.instructure.com/api/v1",
      "token": "...",
      "include_concluded": false,
      "download_page_linked_files": true,
      "download_all_files": false
    }
    """
    api_base = payload.get("api_base")
    token = payload.get("token")
    include_concluded = bool(payload.get("include_concluded", False))
    dl_page_links = bool(payload.get("download_page_linked_files", False))
    dl_all_files = bool(payload.get("download_all_files", False))

    if not api_base or not token:
        raise HTTPException(status_code=400, detail="api_base and token are required.")

    # temp workspace per request
    tmp = tempfile.mkdtemp(prefix="canvas_export_")
    try:
        courses = get_courses(api_base, token, include_concluded=include_concluded)
        index: List[Dict[str, Any]] = []

        for c in courses:
            course_obj = collect_course(api_base, token, c)
            course_json_path = write_course_json(tmp, course_obj)
            index.append({
                "id": course_obj.get("id"),
                "name": course_obj.get("name"),
                "file": os.path.basename(course_json_path),
            })
            cid = course_obj.get("id"); cname = course_obj.get("name") or f"course_{cid}"
            files_dir = os.path.join(tmp, f"{cid}_{re.sub(r'[<>:\"/\\\\|?*\\x00-\\x1F]', '_', cname)}_files")

            if dl_page_links:
                download_page_linked_files_for_course(api_base, token, course_obj, files_dir)
            if dl_all_files:
                download_all_course_files(api_base, token, course_obj, files_dir)

        with open(os.path.join(tmp, "courses_index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2, ensure_ascii=False)

        zip_bytes = make_zip_bytes(tmp)
        filename = "canvas_export.zip"
        return StreamingResponse(io.BytesIO(zip_bytes), media_type="application/zip",
                                 headers={"Content-Disposition": f"attachment; filename={filename}"})
    finally:
        # wipe workspace
        shutil.rmtree(tmp, ignore_errors=True)

