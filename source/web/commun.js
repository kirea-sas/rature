// Utilitaires partagés par les pages de Rature
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const LOGO = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
  <path d="M3 10v4M7 6v12M11 9v6M15 4v16M19 8v8"/></svg>`;

async function api(url, opts = {}) {
  const r = await fetch(url, { ...opts, body: opts.body && typeof opts.body !== 'string' ? JSON.stringify(opts.body) : opts.body,
    method: opts.method || (opts.body ? 'POST' : 'GET') });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.erreur) throw new Error(j.erreur || t('commun.erreur', { n: r.status }));
  return j;
}

function hms(t) {
  t = Math.max(0, Math.floor(t));
  return [t / 3600 | 0, (t / 60 | 0) % 60, t % 60].map(n => String(n).padStart(2, '0')).join(':');
}
function dureeLisible(t) {
  if (!t) return '–';
  const h = Math.floor(t / 3600), m = Math.round((t % 3600) / 60);
  if (h) return LANGUE_UI === 'fr' ? `${h} h ${String(m).padStart(2, '0')}` : `${h} h ${m} min`;
  if (m) return `${m} min`;
  return `${Math.round(t)} s`;
}
function tailleLisible(o) {
  if (o > 1e9) return (o / 1e9).toLocaleString(LOCALE, { maximumFractionDigits: 1 }) + ' ' + t('unite.go');
  if (o > 1e6) return Math.round(o / 1e6) + ' ' + t('unite.mo');
  return Math.round(o / 1e3) + ' ' + t('unite.ko');
}
function dateLisible(iso) {
  if (!iso) return '';
  const d = new Date(iso), maintenant = new Date();
  const jours = Math.round((new Date(maintenant.toDateString()) - new Date(d.toDateString())) / 864e5);
  const heure = d.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
  if (jours === 0) return t('commun.aujourdhui', { h: heure });
  if (jours === 1) return t('commun.hier', { h: heure });
  return d.toLocaleDateString(LOCALE, { day: 'numeric', month: 'short', year: d.getFullYear() === maintenant.getFullYear() ? undefined : 'numeric' });
}

function toast(html, { erreur = false, duree = 5000, actions = [] } = {}) {
  let zone = $('#toasts');
  if (!zone) { zone = document.createElement('div'); zone.id = 'toasts'; document.body.appendChild(zone); }
  const t = document.createElement('div');
  t.className = 'toast' + (erreur ? ' erreur' : '');
  t.innerHTML = `<div>${html}</div>`;
  for (const [txt, fn] of actions) {
    const b = document.createElement('button'); b.textContent = txt; b.onclick = () => { fn(); t.remove(); }; t.appendChild(b);
  }
  zone.appendChild(t);
  if (duree) setTimeout(() => t.remove(), duree);
  return t;
}

function fenetre(html, { surFermeture } = {}) {
  const v = document.createElement('div');
  v.className = 'voile';
  v.innerHTML = `<div class="fenetre">${html}</div>`;
  const fermer = () => { if (!v.isConnected) return; v.remove(); removeEventListener('keydown', esc_); surFermeture?.(); };
  const esc_ = e => { if (e.key === 'Escape') fermer(); };
  v.addEventListener('mousedown', e => { if (e.target === v) fermer(); });
  addEventListener('keydown', esc_);
  document.body.appendChild(v);
  $('input, textarea', v)?.focus();
  return { el: v, fermer };
}

function confirmer(titre, texte, libelle = t('commun.confirmer'), danger = false) {
  return new Promise(res => {
    const f = fenetre(`<h2>${titre}</h2><p>${texte}</p><div class="actions">
      <button class="btn" data-r="0">${t('commun.annuler')}</button>
      <button class="btn ${danger ? 'primaire" style="background:var(--cut);border-color:var(--cut)' : 'primaire'}" data-r="1">${libelle}</button></div>`);
    $$('[data-r]', f.el).forEach(b => b.onclick = () => { f.fermer(); res(b.dataset.r === '1'); });
  });
}

function menu(ancre, items) {
  $$('.menu').forEach(m => m.remove());
  const m = document.createElement('div');
  m.className = 'menu';
  for (const it of items) {
    if (it === '-') { m.appendChild(document.createElement('hr')); continue; }
    const b = document.createElement('button');
    b.textContent = it.libelle;
    if (it.danger) b.className = 'danger';
    b.onclick = () => { m.remove(); it.action(); };
    m.appendChild(b);
  }
  document.body.appendChild(m);
  const r = ancre.getBoundingClientRect();
  m.style.top = (r.bottom + scrollY + 6) + 'px';
  m.style.left = Math.min(r.right - m.offsetWidth, innerWidth - m.offsetWidth - 12) + scrollX + 'px';
  setTimeout(() => addEventListener('mousedown', function f(e) { if (!m.contains(e.target)) { m.remove(); removeEventListener('mousedown', f); } }));
}

// Forme d'onde : `crete` = valeurs 0–255 ; `coul(x)` renvoie la couleur d'une colonne (fraction 0–1)
function dessinerOnde(canvas, crete, coul, { barre = 2, ecart = 1 } = {}) {
  const dpr = devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const c = canvas.getContext('2d');
  c.scale(dpr, dpr);
  if (!crete?.length) return;
  const pas = barre + ecart, n = Math.floor(w / pas);
  for (let i = 0; i < n; i++) {
    const a = Math.floor(i / n * crete.length), b = Math.max(a + 1, Math.floor((i + 1) / n * crete.length));
    let v = 0; for (let k = a; k < b; k++) v = Math.max(v, crete[k]);
    const hh = Math.max(1.5, v / 255 * (h - 2));
    c.fillStyle = coul((i + .5) / n);
    c.beginPath();
    c.roundRect ? c.roundRect(i * pas, (h - hh) / 2, barre, hh, barre / 2) : c.rect(i * pas, (h - hh) / 2, barre, hh);
    c.fill();
  }
}
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

// Dans l'application, un fichier lâché hors d'une zone prévue ne doit pas remplacer la page
addEventListener('dragover', e => e.preventDefault());
addEventListener('drop', e => e.preventDefault());

// ⌘, : réglages (comme dans toutes les apps Mac)
addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key === ',') { e.preventDefault(); location.href = '/reglages'; } });

// Sous-titres SRT tout de suite, sans export audio (calés sur l'enregistrement d'origine)
async function sousTitres(pid, bouton) {
  if (bouton) bouton.disabled = true;
  try {
    const r = await api(`/api/p/${pid}/sous-titres`, { body: {} });
    const noms = r.fichiers.map(f => esc(f.split('/').pop())).join('<br>');
    toast(`${t('st.fait', { dossier: esc(r.dossier.split('/').pop()) })}<br><small>${noms}</small>`, {
      duree: 8000, actions: [[t('st.afficher'), () => api(`/api/p/${pid}/finder`, { body: { fichier: r.fichiers[0] } })]] });
  } catch (e) {
    toast(esc(e.message), { erreur: true });
  } finally {
    if (bouton) bouton.disabled = false;
  }
}

// Lien de retour vers la bonne liste : projets de montage ou sous-titres de film
function lienRetour(p) {
  const a = document.querySelector('a[data-t="commun.projets"]');
  if (!a) return;
  if (p?.genre === 'sous-titres') { a.href = '/sous-titres'; a.dataset.t = 'choix.films'; a.textContent = t('choix.films'); }
  else a.href = '/projets';
}
