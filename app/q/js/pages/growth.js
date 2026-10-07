import {store, apiGet, esc, icon, bindGo, loadAudits, allFindings, skeleton} from '/app/q/js/core.js';
import {isTestProject, relativeChange} from '/app/q/js/workflow.js';
let savedSite = localStorage.getItem('gem-growth-site') || '', days = 28, statusPromise;
const cache = new Map();
export async function growthStatus() {
  if (!statusPromise) statusPromise = apiGet('/api/agency/gsc/status').catch(e => {statusPromise = null; throw e;});
  return statusPromise;
}
export function growthShell() {
  return `<div class="growth-controls"><select aria-label="Search Console property" id="growthSite"><option>Loading properties…</option></select><select aria-label="Measurement period" id="growthDays">${[28,90].map(n=>`<option value="${n}" ${days===n?'selected':''}>Last ${n} days</option>`).join('')}</select></div><div id="growthResult">${skeleton(3)}</div>`;
}
export function growthChart(rows) {
  if (!rows?.length) return '<div class="growth-empty"><p>No daily search activity was returned for this period.</p></div>';
  const max = Math.max(...rows.map(r=>r.clicks),1), w=560,h=155,x=i=>40+i*(w-50)/Math.max(1,rows.length-1),y=v=>h-10-v/max*(h-30);
  const line=rows.map((r,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(r.clicks).toFixed(1)}`).join(' ');
  return `<div class="growth-chart"><svg viewBox="0 0 570 185" role="img" aria-label="Daily organic clicks, ${rows[0].date} to ${rows.at(-1).date}">${[0,.5,1].map(v=>`<line x1="40" y1="${y(v*max)}" x2="550" y2="${y(v*max)}" stroke="var(--line)"/><text x="30" y="${y(v*max)+4}" text-anchor="end">${Math.round(v*max)}</text>`).join('')}<path d="${line} L${x(rows.length-1)},${h-10} L40,${h-10} Z" fill="#64d4bd12"/><path d="${line}" fill="none" stroke="var(--ac)" stroke-width="2.5"/>${[0,Math.floor((rows.length-1)/2),rows.length-1].map(i=>`<text x="${x(i)}" y="177" text-anchor="${i===0?'start':i===rows.length-1?'end':'middle'}">${esc(rows[i].date.slice(5))}</text>`).join('')}</svg></div>`;
}
export async function wireGrowth(root,onData=()=>{}) {
  const box=root.querySelector('#growthResult'),site=root.querySelector('#growthSite'),period=root.querySelector('#growthDays');if(!box)return;
  function unavailable(title,body){box.innerHTML=`<div class="growth-empty">${icon('chart')}<h3>${esc(title)}</h3><p>${esc(body)}</p><button class="btn" data-go="integrations">${icon('plug')} Manage connections</button></div>`;bindGo(box);onData(null);}
  async function update(){
    const selected=site.value;savedSite=selected;days=Number(period.value);localStorage.setItem('gem-growth-site',selected);box.innerHTML=skeleton(3);onData(null);
    const key=`${selected}:${days}`;box.dataset.request=key;
    try{const cached=cache.get(key);const d=cached&&Date.now()-cached.at<300000?cached.data:await apiGet(`/api/agency/gsc/performance?site=${encodeURIComponent(selected)}&days=${days}&series=1`);cache.set(key,{at:Date.now(),data:d});if(!box.isConnected||box.dataset.request!==key)return;
      const change=relativeChange(d.current.clicks,d.previous.clicks);
      box.innerHTML=`<div class="growth-mini"><span><b>${d.current.clicks.toLocaleString()}</b>Organic clicks${change===null?'':` · ${change>0?'+':''}${change}%`}</span><span><b>${d.current.impressions.toLocaleString()}</b>Impressions</span><span><b>${d.current.ctr}%</b>Click-through rate</span></div>${growthChart(d.daily)}<div class="growth-source"><span>Google Search Console · ${esc(d.start_date||'')} — ${esc(d.end_date||'')}</span><span>Compared with previous ${d.days} days</span></div><details class="builder-details"><summary>View daily measurements</summary><div class="tblwrap"><table class="tbl"><thead><tr><th>Date</th><th>Clicks</th><th>Impressions</th></tr></thead><tbody>${(d.daily||[]).map(r=>`<tr><td>${esc(r.date)}</td><td>${r.clicks}</td><td>${r.impressions}</td></tr>`).join('')}</tbody></table></div></details>`;onData(d);
    }catch(e){if(box.isConnected&&box.dataset.request===key)unavailable('Search data unavailable',String(e.message||e));}
  }
  try{const st=await growthStatus();let sites=[...new Set((st.mappings||[]).map(m=>m.site_url))];if(!sites.length&&st.connected){const listed=await apiGet('/api/agency/gsc/sites');sites=(listed.sites||[]).map(s=>s.site_url).filter(Boolean);}if(!site.isConnected)return;
    if(!sites.length){site.innerHTML='<option>No property connected</option>';site.disabled=true;period.disabled=true;unavailable('Connect your search performance','Link a Search Console property to measure real clicks, impressions and search visibility.');return;}
    if(!sites.includes(savedSite))savedSite=sites[0];site.innerHTML=sites.map(s=>`<option value="${esc(s)}" ${s===savedSite?'selected':''}>${esc(s.replace('sc-domain:',''))}</option>`).join('');site.addEventListener('change',update);period.addEventListener('change',update);await update();
  }catch(e){site.innerHTML='<option>Connection unavailable</option>';unavailable('Check your search connection',String(e.message||e));}
}
export async function opportunities(root,projects){
  await loadAudits(projects.map(p=>p.id));if(!root.isConnected)return;const seen=new Set(),findings=allFindings(store.audits,projects).filter(f=>{const k=f.title+'|'+f.project.name;if(seen.has(k))return false;seen.add(k);return true;});
  root.innerHTML=findings.length?findings.slice(0,3).map((f,i)=>`<button class="opportunity" data-go="project/${esc(f.project.id)}"><span class="number">${i+1}</span><span><b>${esc(f.title)}</b><p>${esc(f.project.name)} · ${esc(f.severity)} priority</p></span></button>`).join(''):'<div class="growth-empty"><h3>Build your growth baseline</h3><p>Run a website audit to turn verified findings into a prioritized improvement plan.</p><button class="btn" data-go="builder/seo_campaign">Start website audit</button></div>';bindGo(root);
}
export default async function growth(el){
  statusPromise=null;el.innerHTML=`<div class="page"><div class="phead"><div><h1>Grow your websites.</h1><p>Your results: what search sent you, what to fix next, and who to earn links from. For keyword, competitor and backlink research, use OpenSEO.</p></div><button class="btn pri" data-go="builder/seo_campaign">Start growth campaign</button></div><div class="growth-tabs"><button class="btn" data-go="optimize">${icon('seo')} Website audits</button><button class="btn" data-go="backlinks">${icon('link')} Link outreach</button><button class="btn" data-go="seo">${icon('search')} Research in OpenSEO ${icon('arrowR')}</button></div><div class="command-bottom"><section class="panel wide-growth"><div class="panel-h"><h2>Search performance</h2></div><div class="panel-b">${growthShell()}</div></section><section class="panel"><div class="panel-h"><h2>Next growth opportunities</h2></div><div class="panel-b" id="growthOpportunities">${skeleton(3)}</div><div class="panel-f"><button class="btn" data-go="optimize">View all findings</button></div></section></div></div>`;bindGo(el);await Promise.allSettled([wireGrowth(el),opportunities(el.querySelector('#growthOpportunities'),(store.overview?.projects||[]).filter(p=>!isTestProject(p)))]);
}
