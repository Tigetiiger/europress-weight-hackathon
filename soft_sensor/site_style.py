"""Page shell for the technical documentation: CSS, table-of-contents layout, tooltip and image-viewer scripts."""
from __future__ import annotations

import json
from html import escape

CSS = """
:root{--bg:#fbfaf7;--fg:#1c2026;--muted:#5b6370;--card:#ffffff;--line:#d9dcd3;--rule:#2d3136;--accent:#2f5d8a;
--good:#e3efe3;--good-fg:#1f4d2a;--code:#f0efe9;--tip-bg:#23272e;--tip-fg:#f3f4f6;--mark:#f3efdc}
@media (prefers-color-scheme:dark){:root{--bg:#15181d;--fg:#dde1e6;--muted:#9aa3ad;--card:#1c2026;--line:#333941;
--rule:#c9ced5;--accent:#8fb4dc;--good:#1d3324;--good-fg:#a9dcb5;--code:#242a32;--tip-bg:#f3f4f6;--tip-fg:#1c2026;--mark:#2d2b22}}
*{box-sizing:border-box}html{scroll-padding-top:1.2rem;scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.65 Georgia,"Iowan Old Style","Palatino Linotype",
"Source Serif Pro",serif;-webkit-text-size-adjust:100%}
.layout{display:grid;grid-template-columns:minmax(0,1fr);max-width:1240px;margin:0 auto;padding:0 16px}
@media (min-width:1000px){.layout{grid-template-columns:250px minmax(0,1fr);gap:2.5rem}
nav.toc{position:sticky;top:0;align-self:start;max-height:100vh;overflow-y:auto;padding:1.6rem 0 2rem}}
nav.toc{font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:1rem 0 .4rem;border-bottom:1px solid var(--line)}
@media (min-width:1000px){nav.toc{border-bottom:0}}
nav.toc p{margin:0 0 .5rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;font-size:.75rem;color:var(--muted)}
nav.toc ol{list-style:none;margin:0;padding:0}nav.toc ol ol{padding-left:1.4rem;margin:.15rem 0 .35rem}
nav.toc li{margin:.15rem 0}nav.toc a{color:var(--fg);text-decoration:none}nav.toc a:hover{color:var(--accent);text-decoration:underline}
nav.toc .n{color:var(--muted);display:inline-block;min-width:1.6em}nav.toc ol ol .n{min-width:2.4em}
@media (max-width:999px){nav.toc ol ol{display:none}nav.toc ol{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0 1rem}}
main{min-width:0;padding-bottom:3rem}
section{padding-top:2.4rem}section.doc-head{padding-top:1.8rem}
h1{font-size:clamp(1.55rem,3.2vw,2.15rem);line-height:1.25;margin:.4rem 0 .6rem;font-weight:700;letter-spacing:-.01em}
h2{font-size:1.5rem;line-height:1.3;margin:0 0 1rem;padding-bottom:.35rem;border-bottom:2px solid var(--rule)}
h3{font-size:1.15rem;margin:2rem 0 .6rem}h4{font-size:1rem;margin:1.5rem 0 .4rem;font-family:system-ui,sans-serif}
h2 .n,h3 .n,h4 .n{color:var(--muted);font-weight:400;margin-right:.5em}
a{color:var(--accent)}p,li,dd{max-width:72ch}.muted{color:var(--muted)}
.abstract{font-size:1.02rem;border-left:3px solid var(--rule);padding:.2rem 0 .2rem 1rem;margin:1.2rem 0}
.meta{font:14px/1.5 system-ui,sans-serif;color:var(--muted);margin:.4rem 0 1rem}
.notice{border:1px solid var(--line);background:var(--code);padding:.6rem .9rem;border-radius:4px;font-family:system-ui,sans-serif;font-size:.92rem}
.note{color:var(--muted);font-style:italic;border:1px dashed var(--line);padding:.5rem .8rem;border-radius:4px}
.tcap,figcaption{font:.86rem/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--muted)}
.tcap{margin:1.4rem 0 .35rem;max-width:none}.tcap b,figcaption b{color:var(--fg);font-weight:600}
.tbl{overflow-x:auto;margin:0 0 1rem;max-width:100%}
table{border-collapse:collapse;font:.875rem/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;width:100%}
th,td{padding:.32rem .65rem;text-align:right;border-bottom:1px solid var(--line);vertical-align:top;white-space:nowrap}
th{border-bottom:2px solid var(--rule);font-weight:600;text-align:right;background:transparent;white-space:normal;min-width:6ch}
td:first-child,th:first-child,td.l,th.l{text-align:left}td.l,th.l{white-space:normal;min-width:12ch}
td.good{background:var(--good);color:var(--good-fg);font-weight:600}
th.lift,td.lift{background:var(--mark)}tr.key td{font-weight:700}tr.fam td{background:var(--code);font-weight:600;text-align:left}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code);padding:.05rem .3rem;border-radius:3px;font-size:.88em}
pre{background:var(--code);padding:.8rem 1rem;border-radius:4px;overflow-x:auto;font-size:.82rem;line-height:1.5;border-left:3px solid var(--line)}
pre code{padding:0;background:none;font-size:inherit}
.figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:1.2rem;margin:1rem 0 1.4rem}
.figs.one{grid-template-columns:minmax(0,1fr);max-width:860px}
figure{margin:0}figure img{max-width:100%;height:auto;display:block;margin:0 auto;background:#fff;border:1px solid var(--line);border-radius:3px}
figcaption{margin-top:.45rem}a.zoom img{cursor:zoom-in}
.flow{display:flex;flex-wrap:wrap;align-items:stretch;gap:.5rem;margin:1rem 0;font-family:system-ui,sans-serif}
.flow .box{flex:1 1 180px;background:var(--card);border:1px solid var(--line);border-top:3px solid var(--rule);padding:.6rem .8rem;font-size:.86rem}
.flow .box b{display:block;font-size:.95rem;margin-bottom:.25rem}.flow .box ul{margin:.2rem 0;padding-left:1.1rem}
.flow .arrow{align-self:center;font-size:1.5rem;color:var(--muted)}
@media (max-width:760px){.flow{flex-direction:column}.flow .arrow{transform:rotate(90deg);text-align:center}}
.bars{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:1rem;font-family:system-ui,sans-serif}
.bar-card{background:var(--card);border:1px solid var(--line);padding:.6rem .8rem}
.bar-card h5{margin:.1rem 0 .5rem;font-size:.92rem}.bar{display:grid;grid-template-columns:11rem 1fr 3.2rem;gap:.4rem;align-items:center;font-size:.8rem;margin:.15rem 0}
.bar code{font-size:.78rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bar .track{background:var(--code);height:.7rem}
.bar .fill{background:var(--accent);height:100%}.bar span{text-align:right;color:var(--muted)}
dl.gloss{font-size:.95rem}dl.gloss dt{font-weight:700;margin-top:.7rem}dl.gloss dt code{font-weight:700}
dl.gloss dt .cat{font:.72rem/1 system-ui,sans-serif;color:var(--muted);font-weight:400;margin-left:.5em;text-transform:uppercase;letter-spacing:.04em}
dl.gloss dd{margin:.1rem 0 0 0}
ul.dl{list-style:none;padding:0;margin:0}ul.dl li{border-bottom:1px solid var(--line);padding:.6rem 0;max-width:none}
ul.dl li a{font-weight:600}ul.dl .sz{font:.8rem system-ui,sans-serif;color:var(--muted);margin-left:.5em}ul.dl .d{display:block;font-size:.92rem;max-width:80ch}
.term{border-bottom:1px dotted var(--muted);cursor:help;text-decoration:none;color:inherit}
.term:focus{outline:2px solid var(--accent);outline-offset:1px}
#tip{position:fixed;z-index:60;max-width:min(340px,calc(100vw - 16px));background:var(--tip-bg);color:var(--tip-fg);padding:.5rem .7rem;
border-radius:5px;font:.83rem/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.25);pointer-events:none}
#tip[hidden]{display:none}#tip b{display:block;margin-bottom:.15rem}
.lb{position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.88);display:flex;flex-direction:column;align-items:center;justify-content:center;padding:16px;cursor:zoom-out}
.lb[hidden]{display:none}.lb img{max-width:100%;max-height:calc(100vh - 7rem);object-fit:contain;background:#fff;border-radius:4px}
.lb-x{position:fixed;top:12px;right:12px;width:3rem;height:3rem;border-radius:50%;border:0;background:#fff;color:#000;font-size:1.8rem;line-height:1;cursor:pointer}
.lb-cap{color:#eee;font:.9rem system-ui,sans-serif;margin:.6rem 0 0;text-align:center;max-width:90ch}.lb-cap a{color:#9cc8ff}
nav.toc.spy li.has-sub:not(.open)>ol{display:none}
nav.toc a[aria-current]{color:var(--accent);font-weight:700}nav.toc ol ol a[aria-current]{font-weight:600}
nav.toc a[aria-current] .n{color:var(--accent)}
details.gallery{margin:1.2rem 0 .4rem;border:1px solid var(--line);border-radius:6px;background:var(--card);
font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
details.gallery>summary{cursor:pointer;padding:.75rem 1rem;font-weight:600;font-size:1rem}
details.gallery>summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
details.gallery[open]>summary{border-bottom:1px solid var(--line)}
.gnote{margin:.8rem 1rem .2rem;color:var(--muted);font-size:.88rem;max-width:none}
ol.glist{list-style:none;margin:0;padding:.6rem 1rem 1rem;display:grid;gap:1.1rem 1.4rem;
grid-template-columns:repeat(auto-fill,minmax(min(100%,360px),1fr))}
li.gitem{max-width:none;margin:0;border-top:1px solid var(--line);padding-top:.8rem;min-width:0}
li.gitem img{width:100%;height:auto;display:block;background:#fff;border:1px solid var(--line);border-radius:3px;cursor:zoom-in}
li.gitem img[data-src]{min-height:180px}
.gtext p{margin:.5rem 0 0;font-size:.86rem;line-height:1.5;max-width:none}.gtext p.gtitle{font-weight:600}
.gtext .gtitle a{text-decoration:none}
figcaption .fdesc{display:block;margin-top:.3rem}
.lb-res{font-style:normal;color:#bbb}
footer{border-top:1px solid var(--line);margin-top:3rem;padding-top:1rem;font:.85rem/1.5 system-ui,sans-serif;color:var(--muted)}
"""

LIGHTBOX = """<div id="lb" class="lb" hidden role="dialog" aria-modal="true" aria-label="Enlarged image">
<button class="lb-x" type="button" aria-label="Close image">&times;</button><img alt="">
<p class="lb-cap"><span></span> · <a target="_blank" rel="noopener">open full size</a> <em class="lb-res"></em> · click anywhere
or press Esc to close</p></div>
<script>(function(){var lb=document.getElementById("lb");if(!lb)return;var im=lb.querySelector("img"),
cap=lb.querySelector(".lb-cap span"),full=lb.querySelector(".lb-cap a"),res=lb.querySelector(".lb-res"),last=null,cur=null;
function close(){lb.hidden=true;cur=null;im.removeAttribute("src");document.body.style.overflow="";if(last)last.focus();}
function show(a){var hi=a.getAttribute("href"),lo=a.getAttribute("data-lo")||hi;cur=last=a;im.src=lo;
im.alt=a.dataset.cap||"";cap.textContent=a.dataset.cap||"";full.href=hi;res.textContent="";lb.hidden=false;
document.body.style.overflow="hidden";lb.querySelector(".lb-x").focus();if(hi===lo)return;
res.textContent="(loading high resolution…)";var pre=new Image();
pre.onload=function(){if(cur===a){im.src=hi;res.textContent="(high resolution)";}};
pre.onerror=function(){if(cur===a){full.href=lo;res.textContent="";}};pre.src=hi;}
document.addEventListener("click",function(e){var a=e.target.closest&&e.target.closest("a.zoom");
if(a&&!e.ctrlKey&&!e.metaKey&&!e.shiftKey){e.preventDefault();show(a);return;}
if(!lb.hidden&&!(e.target.closest&&e.target.closest(".lb-cap a")))close();});
document.addEventListener("keydown",function(e){if(!lb.hidden&&e.key==="Escape")close();});})();</script>"""

TOC_SPY = """<script>(function(){var nav=document.querySelector("nav.toc");if(!nav)return;var items=[],groups=[];
[].forEach.call(nav.querySelectorAll("nav.toc > ol > li"),function(li,i){var a=li.querySelector("a"),
sub=li.querySelector("ol");if(!a)return;var g={li:li,a:a,sub:sub};groups.push(g);
if(sub){sub.id="toc-sub-"+i;a.setAttribute("aria-controls",sub.id);a.setAttribute("aria-expanded","false");
li.classList.add("has-sub");}var h=document.getElementById(a.hash.slice(1));if(h)items.push({h:h,a:a,g:g});
if(sub)[].forEach.call(sub.querySelectorAll("a"),function(s){var hs=document.getElementById(s.hash.slice(1));
if(hs)items.push({h:hs,a:s,g:g,sub:true});});});
if(!items.length)return;nav.classList.add("spy");var cur=null,tick=false;
function pick(){var y=Math.min(140,window.innerHeight*0.3),best=null;
if(window.innerHeight+window.scrollY>=document.documentElement.scrollHeight-2)best=items[items.length-1];
else for(var i=0;i<items.length;i++){if(items[i].h.getBoundingClientRect().top<=y)best=items[i];else break;}return best;}
function update(){tick=false;var it=pick();if(it===cur)return;cur=it;var main=it?it.g:null;
groups.forEach(function(g){var on=g===main;g.li.classList.toggle("open",on);if(g.sub)g.a.setAttribute("aria-expanded",on?"true":"false");
if(on)g.a.setAttribute("aria-current","location");else g.a.removeAttribute("aria-current");});
[].forEach.call(nav.querySelectorAll("ol ol a[aria-current]"),function(a){a.removeAttribute("aria-current");});
if(it&&it.sub)it.a.setAttribute("aria-current","location");var act=it?it.a:null;
if(act&&nav.scrollHeight>nav.clientHeight){var r=act.getBoundingClientRect(),n=nav.getBoundingClientRect();
if(r.top<n.top+40||r.bottom>n.bottom-40)nav.scrollTop+=r.top-n.top-n.height/3;}}
function req(){if(!tick){tick=true;requestAnimationFrame(update);}}
window.addEventListener("scroll",req,{passive:true});window.addEventListener("resize",req);
window.addEventListener("hashchange",req);update();})();</script>"""

TOOLTIP = """<div id="tip" role="tooltip" hidden></div>
<script>(function(){var el=document.getElementById("terms"),tip=document.getElementById("tip");if(!el||!tip)return;
var T=JSON.parse(el.textContent),cur=null;
function show(t){var k=t.getAttribute("data-term");if(!(k in T))return;tip.innerHTML="";var b=document.createElement("b");
b.textContent=k;tip.appendChild(b);tip.appendChild(document.createTextNode(T[k]));tip.hidden=false;
if(cur&&cur!==t)cur.removeAttribute("aria-describedby");cur=t;t.setAttribute("aria-describedby","tip");
var r=t.getBoundingClientRect(),w=tip.offsetWidth,h=tip.offsetHeight,vw=window.innerWidth,vh=window.innerHeight;
var x=Math.min(Math.max(8,r.left),vw-w-8),y=r.bottom+6;if(y+h>vh-8)y=r.top-h-6;if(y<8)y=8;
tip.style.left=x+"px";tip.style.top=y+"px";}
function hide(){if(cur)cur.removeAttribute("aria-describedby");cur=null;tip.hidden=true;}
function term(e){return e.target&&e.target.closest?e.target.closest(".term"):null;}
document.addEventListener("mouseover",function(e){var t=term(e);if(t&&t!==cur)show(t);});
document.addEventListener("mouseout",function(e){var t=term(e);if(t&&t===cur&&!(e.relatedTarget&&t.contains(e.relatedTarget))
&&window.matchMedia("(hover:hover)").matches)hide();});
document.addEventListener("focusin",function(e){var t=term(e);if(t)show(t);});
document.addEventListener("focusout",function(e){var t=term(e);if(t&&t===cur)hide();});
document.addEventListener("click",function(e){var t=term(e);if(t){e.preventDefault();show(t);}else if(cur)hide();});
document.addEventListener("keydown",function(e){if(e.key==="Escape")hide();});
window.addEventListener("scroll",function(){if(cur)show(cur);},{passive:true});
window.addEventListener("resize",function(){if(cur)show(cur);});})();</script>"""


def terms_json(glossary: dict[str, str]) -> str:
    """The glossary as an inline JSON block read by the tooltip script."""
    data = json.dumps(glossary, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/json" id="terms">{data}</script>'


def page(title: str, body: str, nav: str = "", glossary: dict[str, str] | None = None) -> str:
    extra = (terms_json(glossary) + TOOLTIP) if glossary else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title>'
            f'<meta name="color-scheme" content="light dark"><style>{CSS}</style></head><body>'
            f'<div class="layout">{nav}<main>{body}</main></div>{extra}{LIGHTBOX}{TOC_SPY if nav else ""}</body></html>')
