"""Builds the single-file HTML report (thumbnails inlined, no external assets)."""

import html
import json
import os
from datetime import datetime
from pathlib import Path

from . import __version__
from .config import DOC_CATEGORIES, LOW_CONFIDENCE_PHOTO_CATEGORIES, PHOTO_CATEGORIES

SKIP = "__skip__"


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _where(rec) -> str:
    root = rec["root"]
    rel = os.path.relpath(os.path.dirname(rec["path"]), root)
    base = os.path.basename(root.rstrip(os.sep)) or root
    return base if rel == "." else os.path.join(base, rel)


def _uri(path) -> str:
    try:
        return Path(path).as_uri()
    except ValueError:
        return ""


def _options(categories, selected) -> str:
    out = [f'<option value="{_e(k)}"{" selected" if k == selected else ""}>'
           f'{_e(v[1])}</option>' for k, v in categories.items()]
    out.append(f'<option value="{SKIP}">Don’t move</option>')
    return "".join(out)


# ------------------------------------------------------------ suggestions ----
def photo_suggestion(r):
    """Reasons this photo is pre-checked for cleanup (empty list = keep)."""
    why = []
    if r.get("dup_group") and not r.get("is_keeper"):
        why.append("duplicate copy")
    why += r.get("low_quality") or []
    if r.get("category") == "random" and not r.get("error"):
        why.append("low-confidence “random”")
    return why


def doc_suggestion(r):
    why = []
    if r.get("dup_group") and not r.get("is_keeper"):
        why.append("duplicate copy")
    return why


# -------------------------------------------------------------- rendering ----
def _photo_card(r, i):
    why = photo_suggestion(r)
    badges = []
    if r.get("dup_group"):
        badges.append(f'<span class="b b-keep">Best copy</span>' if r.get("is_keeper")
                      else '<span class="b b-dup">Duplicate</span>')
    for q in r.get("low_quality") or []:
        badges.append(f'<span class="b b-lq">{_e(q)}</span>')
    if r.get("category") in LOW_CONFIDENCE_PHOTO_CATEGORIES:
        badges.append('<span class="b b-lc">low confidence</span>')
    img = (f'<img src="{r["thumb"]}" alt="">' if r.get("thumb")
           else '<div class="noimg">no preview</div>')
    dims = f'{r.get("width", 0)}×{r.get("height", 0)} · ' if r.get("width") else ""
    title = f'{r["path"]}\nWhy: {r.get("reason", "")}\nSharpness: {r.get("sharpness", 0)} · faces: {r.get("faces", 0)} · OCR: {r.get("ocr", "")}'
    if r.get("error"):
        title += f'\nError: {r["error"]}'
    return f'''<figure class="card item{' sugg' if why else ''}" data-id="{i}">
<a class="thumb" href="{_e(_uri(r['path']))}" target="_blank" title="{_e(title)}">{img}</a>
<figcaption>
<div class="fname" title="{_e(r['path'])}">{_e(r['name'])}</div>
<div class="meta">{_e(_where(r))}</div>
<div class="meta">{dims}{human_size(r['size'])}</div>
<div class="badges">{''.join(badges)}</div>
<label class="del"><input type="checkbox" class="chk" data-id="{i}"{' checked' if why else ''}> Clean up (delete)</label>
{('<div class="why">Suggested: ' + _e(', '.join(why)) + '</div>') if why else ''}
<select class="cat" data-id="{i}" aria-label="Move to">{_options(PHOTO_CATEGORIES, r['category'])}</select>
</figcaption></figure>'''


def _doc_row(r, i):
    why = doc_suggestion(r)
    badges = []
    if r.get("dup_group"):
        badges.append('<span class="b b-keep">Best copy</span>' if r.get("is_keeper")
                      else '<span class="b b-dup">Duplicate</span>')
    if r.get("confidence", 1) < 0.5:
        badges.append('<span class="b b-lc">low confidence</span>')
    if r.get("error"):
        badges.append(f'<span class="b b-lq" title="{_e(r["error"])}">unreadable</span>')
    elif not r.get("words"):
        badges.append('<span class="b b-lq">no text found</span>')
    modified = datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d")
    return f'''<tr class="item{' sugg' if why else ''}" data-id="{i}">
<td><input type="checkbox" class="chk" data-id="{i}"{' checked' if why else ''} aria-label="Clean up"></td>
<td><a href="{_e(_uri(r['path']))}" target="_blank" class="fname" title="{_e(r['path'])}">{_e(r['name'])}</a>
<div class="meta">{_e(_where(r))}</div></td>
<td class="num">{human_size(r['size'])}<div class="meta">{modified}</div></td>
<td>{''.join(badges)}<div class="meta">{_e(r.get('reason', ''))}{(' · ' + str(r['words']) + ' words') if r.get('words') else ''}</div>
{('<div class="why">' + _e(', '.join(why)) + '</div>') if why else ''}</td>
<td><select class="cat" data-id="{i}" aria-label="Move to">{_options(DOC_CATEGORIES, r['category'])}</select></td>
</tr>'''


def _group_by_category(recs, categories):
    """category -> list of blocks; a block is either ('group', [ids]) or ('single', id)."""
    out = {k: [] for k in categories}
    seen_groups, by_group = set(), {}
    for i, r in recs:
        if r.get("dup_group"):
            by_group.setdefault(r["dup_group"], []).append((i, r))
    for i, r in recs:
        g = r.get("dup_group")
        cat = r["category"] if r["category"] in out else list(out)[-1]
        if g:
            if g in seen_groups:
                continue
            seen_groups.add(g)
            members = list(by_group[g])
            members.sort(key=lambda t: (not t[1].get("is_keeper"), t[1]["path"]))
            out[cat].append(("group", members))
        else:
            out[cat].append(("single", (i, r)))
    for k in out:  # duplicate groups first, then singles
        out[k].sort(key=lambda b: 0 if b[0] == "group" else 1)
    return out


def _group_header(members, noun):
    keeper = next((r for _, r in members if r.get("is_keeper")), members[0][1])
    exact = members[0][1].get("dup_exact")
    same_text = members[0][1].get("text_hash") and len({r.get("text_hash") for _, r in members}) == 1
    kind = ("identical files" if exact else "copies with identical text" if same_text
            else "near-identical " + noun)
    saved = sum(r["size"] for _, r in members if not r.get("is_keeper"))
    return (f'<div class="ghead"><strong>Duplicate group</strong> · {len(members)} {kind} · '
            f'keeping <em>{_e(keeper["name"])}</em> · {human_size(saved)} reclaimable'
            f'<span class="gwarn" hidden>⚠ every copy is marked — nothing will be kept</span></div>')


def _photo_section(photo_items):
    if not photo_items:
        return '<p class="empty">No photos found.</p>'
    parts = []
    blocks = _group_by_category(photo_items, PHOTO_CATEGORIES)
    for cat, (label, dest) in PHOTO_CATEGORIES.items():
        bl = blocks[cat]
        if not bl:
            continue
        n = sum(len(b[1]) if b[0] == "group" else 1 for b in bl)
        parts.append(f'<section class="cat-sec" id="photos-{cat}"><h3>{_e(label)} <span class="count">{n}</span>'
                     f'<span class="dest">→ {_e(dest)}</span>'
                     f'<span class="bulk"><button data-bulk="1">mark all</button><button data-bulk="0">unmark all</button></span></h3>'
                     f'<div class="grid">')
        for kind, payload in bl:
            if kind == "group":
                cards = "".join(_photo_card(r, i) for i, r in payload)
                parts.append(f'<div class="dgroup" data-group="p{payload[0][1]["dup_group"]}">'
                             f'{_group_header(payload, "photos")}<div class="grid">{cards}</div></div>')
            else:
                i, r = payload
                parts.append(_photo_card(r, i))
        parts.append("</div></section>")
    return "".join(parts)


def _doc_section(doc_items):
    if not doc_items:
        return '<p class="empty">No documents found.</p>'
    parts = []
    blocks = _group_by_category(doc_items, DOC_CATEGORIES)
    head = ('<thead><tr><th>Clean&nbsp;up</th><th>File</th><th class="num">Size</th>'
            '<th>Details</th><th>Move to</th></tr></thead>')
    for cat, (label, dest) in DOC_CATEGORIES.items():
        bl = blocks[cat]
        if not bl:
            continue
        n = sum(len(b[1]) if b[0] == "group" else 1 for b in bl)
        parts.append(f'<section class="cat-sec" id="docs-{cat}"><h3>{_e(label)} <span class="count">{n}</span>'
                     f'<span class="dest">→ {_e(dest)}</span>'
                     f'<span class="bulk"><button data-bulk="1">mark all</button><button data-bulk="0">unmark all</button></span></h3>'
                     f'<div class="tablewrap"><table>{head}')
        for kind, payload in bl:
            if kind == "group":
                rows = "".join(_doc_row(r, i) for i, r in payload)
                parts.append(f'<tbody class="dgroup" data-group="d{payload[0][1]["dup_group"]}">'
                             f'<tr class="grow"><td colspan="5">{_group_header(payload, "documents")}</td></tr>{rows}</tbody>')
            else:
                i, r = payload
                parts.append(f"<tbody>{_doc_row(r, i)}</tbody>")
        parts.append("</table></div></section>")
    return "".join(parts)


def _nav(items, categories, prefix):
    counts = {}
    for _, r in items:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    return "".join(f'<a href="#{prefix}-{k}">{_e(v[0].replace(" (low confidence)", ""))} <b>{counts[k]}</b></a>'
                   for k, v in categories.items() if counts.get(k))


def write_report(path, photo_recs, doc_recs, meta):
    photo_items = [(f"p{i}", r) for i, r in enumerate(photo_recs)]
    doc_items = [(f"d{i}", r) for i, r in enumerate(doc_recs)]
    data = {}
    for i, r in photo_items + doc_items:
        data[i] = {"path": r["path"], "root": r["root"], "size": r["size"], "mtime": r["mtime"],
                   "kind": r["kind"], "group": (("p" if r["kind"] == "image" else "d") + str(r["dup_group"]))
                   if r.get("dup_group") else None}
    dest_map = {"image": {k: v[1] for k, v in PHOTO_CATEGORIES.items()},
                "document": {k: v[1] for k, v in DOC_CATEGORIES.items()}}
    payload = {"items": data, "dest": dest_map, "roots": meta["roots"], "created": meta["created"],
               "version": __version__}
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    n_sugg = sum(1 for _, r in photo_items if photo_suggestion(r)) + sum(1 for _, r in doc_items if doc_suggestion(r))
    n_lq = sum(1 for _, r in photo_items if r.get("low_quality"))
    errors = [r for _, r in photo_items + doc_items if r.get("error")]
    roots_html = "".join(f"<li><code>{_e(r)}</code></li>" for r in meta["roots"])

    tiles = [
        (len(photo_items), "photos"), (len(doc_items), "documents"),
        (meta["photo_groups"] + meta["doc_groups"], "duplicate groups"),
        (n_lq, "low-quality photos"), (n_sugg, "suggested for cleanup"),
    ]
    tiles_html = "".join(f'<div class="tile"><div class="tv">{v}</div><div class="tl">{_e(l)}</div></div>' for v, l in tiles)

    errors_html = ""
    if errors:
        errors_html = ('<details class="errs"><summary>' + str(len(errors)) + ' file(s) could not be read</summary><ul>' +
                       "".join(f'<li><code>{_e(r["path"])}</code> — {_e(r["error"])}</li>' for r in errors) +
                       "</ul></details>")

    page = (TEMPLATE
            .replace("%%VERSION%%", _e(__version__))
            .replace("%%CREATED%%", _e(meta["created"].replace("T", " ")))
            .replace("%%ROOTS%%", roots_html)
            .replace("%%OCR%%", _e(meta["ocr"]))
            .replace("%%DURATION%%", _e(meta["duration_s"]))
            .replace("%%TILES%%", tiles_html)
            .replace("%%PHOTO_NAV%%", _nav(photo_items, PHOTO_CATEGORIES, "photos"))
            .replace("%%DOC_NAV%%", _nav(doc_items, DOC_CATEGORIES, "docs"))
            .replace("%%ERRORS%%", errors_html)
            .replace("%%PHOTOS%%", _photo_section(photo_items))
            .replace("%%DOCS%%", _doc_section(doc_items))
            .replace("%%DATA%%", blob))
    Path(path).write_text(page, encoding="utf-8")
    return str(path)


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Declutter report</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--ink:#1d2330;--muted:#667085;--line:#e3e6eb;--accent:#2563eb;--accent-ink:#fff;
--dup:#b45309;--dup-bg:#fff7ed;--keep:#047857;--keep-bg:#ecfdf5;--lq:#b91c1c;--lq-bg:#fef2f2;--lc:#6d28d9;--lc-bg:#f5f3ff;--mark:#fee2e2}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--panel:#1b2029;--ink:#e6e9ef;--muted:#98a2b3;--line:#2c3340;--accent:#60a5fa;--accent-ink:#0b1220;
--dup:#fbbf24;--dup-bg:#3a2a0c;--keep:#34d399;--keep-bg:#0d2e24;--lq:#f87171;--lq-bg:#3b1414;--lc:#c4b5fd;--lc-bg:#261c45;--mark:#3b1a1a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding-bottom:120px}
header{background:var(--panel);border-bottom:1px solid var(--line);padding:20px 24px}
h1{margin:0 0 4px;font-size:22px}h2{font-size:19px;margin:28px 0 8px}h3{font-size:15px;margin:22px 0 10px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.wrap{max-width:1400px;margin:0 auto;padding:0 24px}
.sub,.meta{color:var(--muted)}.meta{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tiles{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0 4px}.tile{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:10px 16px;min-width:130px}
.tv{font-size:22px;font-weight:650}.tl{color:var(--muted);font-size:12px}
.notice{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;padding:12px 16px;margin:16px 0}
.notice ul{margin:6px 0 0;padding-left:20px}
nav.jump{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}nav.jump a{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:3px 10px;color:var(--ink);text-decoration:none;font-size:12px}
nav.jump a b{color:var(--muted);font-weight:500}
.count{background:var(--line);border-radius:999px;padding:0 8px;font-size:12px;font-weight:500}
.dest{color:var(--muted);font-weight:400;font-size:12px}
.bulk{margin-left:auto;display:flex;gap:4px}.bulk button,.btn{font:inherit;font-size:12px;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:6px;padding:3px 9px;cursor:pointer}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}
.dgroup .grid{grid-template-columns:repeat(auto-fill,minmax(180px,1fr))}
div.dgroup{grid-column:1/-1;border:2px dashed var(--dup);background:var(--dup-bg);border-radius:12px;padding:10px}
.ghead{font-size:13px;margin:0 2px 8px;display:flex;flex-wrap:wrap;gap:6px;align-items:center}.gwarn{color:var(--lq);font-weight:600}
.card{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.card.marked{outline:2px solid var(--lq);background:var(--mark)}
.thumb{display:flex;align-items:center;justify-content:center;height:160px;background:repeating-conic-gradient(var(--line) 0 25%,transparent 0 50%) 0 0/16px 16px}
.thumb img{max-width:100%;max-height:160px;display:block}.noimg{color:var(--muted);font-size:12px}
figcaption{padding:8px 10px;display:flex;flex-direction:column;gap:3px;min-width:0}
.fname{font-weight:600;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink);text-decoration:none}
.badges{display:flex;flex-wrap:wrap;gap:4px;min-height:4px}
.b{font-size:11px;border-radius:5px;padding:1px 6px;font-weight:600}.b-dup{color:var(--dup);background:var(--dup-bg);border:1px solid var(--dup)}
.b-keep{color:var(--keep);background:var(--keep-bg);border:1px solid var(--keep)}.b-lq{color:var(--lq);background:var(--lq-bg)}.b-lc{color:var(--lc);background:var(--lc-bg)}
label.del{font-size:12px;display:flex;gap:6px;align-items:flex-start;cursor:pointer}.why{color:var(--lq);font-size:12px}
select.cat{font:inherit;font-size:12px;padding:3px;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--ink);max-width:100%}
.tablewrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600}td.num,th.num{text-align:right;white-space:nowrap}
td .fname{display:inline-block;max-width:420px}
tbody.dgroup{border-left:4px solid var(--dup)}tbody.dgroup tr{background:var(--dup-bg)}tr.item.marked td{background:var(--mark)}
tr.grow td{padding:6px 10px}
body.only-marked .item:not(.marked){display:none}body.only-marked div.dgroup:not(:has(.marked)),body.only-marked tbody.dgroup:not(:has(.marked)){display:none}
.errs{margin:14px 0;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 14px}
.bar{position:fixed;left:0;right:0;bottom:0;background:var(--panel);border-top:1px solid var(--line);box-shadow:0 -4px 16px rgba(0,0,0,.08);padding:10px 24px;z-index:5}
.bar .in{max-width:1400px;margin:0 auto;display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center}
.bar .sum{flex:1 1 280px}.bar input[type=text]{font:inherit;font-size:12px;width:320px;max-width:100%;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink)}
.primary{font:inherit;font-weight:600;border:0;border-radius:8px;padding:9px 16px;cursor:pointer;background:var(--accent);color:var(--accent-ink)}
.danger{background:var(--lq);color:#fff}
.empty{color:var(--muted)}code{font-size:12px}
.toast{position:fixed;bottom:90px;left:50%;transform:translateX(-50%);background:var(--ink);color:var(--bg);padding:10px 16px;border-radius:8px;display:none;z-index:6;max-width:90vw}
@media (max-width:640px){.wrap,header{padding-left:14px;padding-right:14px}.grid{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}}
</style></head><body>
<header><div class="wrap" style="padding:0">
<h1>Declutter report</h1>
<div class="sub">Generated %%CREATED%% · declutter %%VERSION%% · OCR: %%OCR%% · took %%DURATION%% s · fully offline, nothing left this computer</div>
<div class="tiles">%%TILES%%</div>
</div></header>
<main class="wrap">
<div class="notice"><strong>Nothing has been moved or deleted.</strong> Review the suggestions below:
checked items are <em>suggested for cleanup</em> (duplicate copies, low-quality shots, low-confidence “random” photos) —
uncheck anything you want to keep and change “Move to” where a category is wrong. Then use the buttons at the bottom to export
a move list and/or a delete list, and apply them from the declutter app (or <code>apply_moves.py</code> / <code>apply_deletions.py</code>).
<div class="sub" style="margin-top:6px">Scanned folders:</div><ul>%%ROOTS%%</ul>
<label style="display:inline-flex;gap:6px;margin-top:8px;cursor:pointer"><input type="checkbox" id="onlyMarked"> Show only items marked for cleanup</label>
</div>
%%ERRORS%%
<h2 id="photos">Photos</h2><nav class="jump">%%PHOTO_NAV%%</nav>
%%PHOTOS%%
<h2 id="documents">Documents</h2><nav class="jump">%%DOC_NAV%%</nav>
%%DOCS%%
</main>
<div class="bar"><div class="in">
<div class="sum"><strong id="delCount">0</strong> marked for deletion (<span id="delSize">0 B</span>) · <strong id="moveCount">0</strong> to move into category folders
<div><label class="sub" style="font-size:12px">Move destination: <input type="text" id="dest" placeholder="default: a “Declutter Organized” folder inside each scanned folder"></label></div></div>
<button class="primary" id="exportMove">Export move list</button>
<button class="primary danger" id="exportDelete">Export delete list</button>
</div></div>
<div class="toast" id="toast"></div>
<script type="application/json" id="dl-data">%%DATA%%</script>
<script>
(function(){
const D=JSON.parse(document.getElementById('dl-data').textContent);
const chk=[...document.querySelectorAll('input.chk')], sel=[...document.querySelectorAll('select.cat')];
const byId=id=>document.querySelector('.item[data-id="'+id+'"]');
function hs(n){const u=['B','KB','MB','GB'];let i=0;while(n>=1024&&i<3){n/=1024;i++}return (i?n.toFixed(1):n)+' '+u[i]}
function state(){const del=new Set(chk.filter(c=>c.checked).map(c=>c.dataset.id));const cat={};sel.forEach(s=>cat[s.dataset.id]=s.value);return {del,cat}}
function update(){
  const {del,cat}=state();let size=0;del.forEach(id=>size+=D.items[id].size);
  let mv=0;Object.keys(D.items).forEach(id=>{if(!del.has(id)&&cat[id]!=='__skip__')mv++});
  document.getElementById('delCount').textContent=del.size;document.getElementById('delSize').textContent=hs(size);
  document.getElementById('moveCount').textContent=mv;
  chk.forEach(c=>{const el=byId(c.dataset.id);if(el)el.classList.toggle('marked',c.checked)});
  document.querySelectorAll('.dgroup').forEach(g=>{const cs=[...g.querySelectorAll('input.chk')];
    const w=g.querySelector('.gwarn');if(w)w.hidden=!(cs.length&&cs.every(c=>c.checked))});
}
chk.forEach(c=>c.addEventListener('change',update));sel.forEach(s=>s.addEventListener('change',update));
document.querySelectorAll('.bulk button').forEach(b=>b.addEventListener('click',()=>{
  const sec=b.closest('section');sec.querySelectorAll('input.chk').forEach(c=>c.checked=b.dataset.bulk==='1');update()}));
document.getElementById('onlyMarked').addEventListener('change',e=>document.body.classList.toggle('only-marked',e.target.checked));
function toast(t){const el=document.getElementById('toast');el.textContent=t;el.style.display='block';clearTimeout(toast.t);toast.t=setTimeout(()=>el.style.display='none',6000)}
function download(name,obj){
  const blob=new Blob([JSON.stringify(obj,null,2)],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;document.body.appendChild(a);a.click();
  setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},1000);
}
function base(type){return {tool:'declutter',version:D.version,list_type:type,exported:new Date().toISOString(),
  report_created:D.created,scanned_folders:D.roots,items:[]}}
document.getElementById('exportMove').addEventListener('click',()=>{
  const {del,cat}=state();const out=base('move');const dest=document.getElementById('dest').value.trim();
  if(dest)out.destination=dest;
  Object.keys(D.items).forEach(id=>{const it=D.items[id];if(del.has(id)||cat[id]==='__skip__')return;
    out.items.push({path:it.path,root:it.root,size:it.size,mtime:it.mtime,kind:it.kind,category:cat[id],subfolder:D.dest[it.kind][cat[id]]})});
  if(!out.items.length){toast('Nothing to move — every item is either marked for deletion or set to “Don’t move”.');return}
  download('declutter_move_list.json',out);toast('Saved declutter_move_list.json ('+out.items.length+' items) to your downloads folder.');
});
document.getElementById('exportDelete').addEventListener('click',()=>{
  const {del}=state();const out=base('delete');
  const groups={};del.forEach(id=>{const g=D.items[id].group;if(g)groups[g]=(groups[g]||0)+1});
  const whole=Object.keys(groups).filter(g=>groups[g]===Object.values(D.items).filter(x=>x.group===g).length);
  if(whole.length&&!confirm(whole.length+' duplicate group(s) have EVERY copy marked, so no copy would be kept. Export anyway?'))return;
  del.forEach(id=>{const it=D.items[id];out.items.push({path:it.path,root:it.root,size:it.size,mtime:it.mtime,kind:it.kind})});
  if(!out.items.length){toast('Nothing is marked for deletion.');return}
  download('declutter_delete_list.json',out);toast('Saved declutter_delete_list.json ('+out.items.length+' items). Nothing is deleted until you apply it.');
});
update();
})();
</script>
</body></html>
"""
