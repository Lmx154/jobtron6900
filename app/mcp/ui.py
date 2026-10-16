"""Small MCP review surface: every answer and letter stays visible."""
from html import escape

APPLICATIONS_UI_URI = "ui://jobtron/applications.html"

_APPLICATIONS_UI_TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jobtron applications</title><style>
:root { color-scheme: light dark; font: 15px/1.6 system-ui,sans-serif; }
body { margin:0; padding:24px; background:light-dark(#f6f5f1,#191c1b); color:light-dark(#233b34,#ecf0eb); }
h1 { font-size:26px; margin:0 0 8px; } h2 {font-size:18px;} p {margin:8px 0;}
article {padding:18px; margin:14px 0; border:1px solid #81978c66; border-radius:12px;}
a {color:light-dark(#17634d,#9fe0c2);} .meta {font-size:13px;opacity:.8;} pre {white-space:pre-wrap; overflow-wrap:anywhere; font:inherit;}
</style></head><body><main id="app" aria-live="polite"><h1>Your applications</h1><p><a href="__URL__/applications" target="_blank" rel="noreferrer">Open Jobtron</a></p></main>
<script>
const root=document.getElementById('app');
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const base=__BASE_JSON__;
function link(path,label){return `<a href="${esc(base+path)}" target="_blank" rel="noreferrer">${esc(label)}</a>`;}
function render(data){
 if(!data)return;
 if(data.view==='setup'){
  root.innerHTML=`<h1>Resume & answers</h1><p>Upload your original resume and save reusable answers. Your resume is used as a source for drafts.</p>${link('/profile','Open resume & answers')}`;
 }else if(data.view==='application'){
  const a=data.application??{};
  const answers=(data.questions??[]).map(q=>{const v=q.answers?.[0];return `<article><h2>${esc(q.question)}</h2><p class="meta">${v?(v.approved?'Reviewed':'Draft — needs review')+' · version '+esc(v.version):'Unanswered'}</p><pre>${esc(v?.content??'No answer saved.')}</pre></article>`;}).join('');
  const letter=data.cover_letters?.[0];
  root.innerHTML=`<h1>${esc(a.formal_title)}</h1><p>${esc(a.company_name)} · ${a.active?'Active':'Inactive'}</p><p class="meta">Created ${esc(a.created_at)} UTC</p>${link('/applications/'+a.id,'Edit, regenerate or download a dated bundle')}<h2>Application answers</h2>${answers||'<p>No questions saved.</p>'}<article><h2>Cover letter</h2><p class="meta">${letter?(letter.approved?'Reviewed':'Draft — needs review')+' · version '+esc(letter.version):'Not drafted'}</p><pre>${esc(letter?.text_content??'No cover letter saved.')}</pre></article><p>Generated material stays unapproved until you review it.</p>`;
 }else{
  root.innerHTML=`<h1>Your applications</h1><p>Job information, answers and cover letters in one dated record.</p>${link('/applications','Open application tracker')}`+(data.applications??[]).map(a=>`<article><h2>${link('/applications/'+a.id,a.formal_title)}</h2><p>${esc(a.company_name)} · ${a.active?'Active':'Inactive'}</p><p class="meta">Created ${esc(a.created_at)} UTC</p></article>`).join('');
 }
}
window.addEventListener('message',event=>{if(event.source!==window.parent)return;const m=event.data;if(m?.method==='ui/notifications/tool-result')render(m.params?.structuredContent);});
if(window.openai?.toolOutput)render(window.openai.toolOutput);
window.parent.postMessage({jsonrpc:'2.0',id:1,method:'ui/initialize',params:{appInfo:{name:'jobtron6900',version:'0.2.0'},capabilities:{}}},'*');
window.addEventListener('message',event=>{if(event.source===window.parent&&event.data?.id===1&&event.data?.result)window.parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/initialized'},'*');});
</script></body></html>'''


def build_applications_ui(frontend_base_url: str) -> str:
    import json
    url = frontend_base_url.rstrip('/')
    return _APPLICATIONS_UI_TEMPLATE.replace('__URL__', escape(url, quote=True)).replace('__BASE_JSON__', json.dumps(url).replace('<', '\\u003c'))


APPLICATIONS_UI = build_applications_ui('http://127.0.0.1:8765')
