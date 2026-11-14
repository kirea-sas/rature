// Non-régression de l'éditeur : couper (⌫), annuler (⌘Z), rétablir (⇧⌘Z) — sur une instance de démo
window.__log = [];
navigator.sendBeacon = () => true;
window.addEventListener('error', e => __log.push('ERREUR ' + e.message + ' @' + e.lineno));
(async () => {
  const L = m => __log.push(m), att = ms => new Promise(r => setTimeout(r, ms));
  await att(1000);
  const k0 = spans.findIndex((s, k) => !sup.has(J[k].i) && !J[k].p && k > 10);
  const r = document.createRange(); r.setStart(spans[k0].firstChild, 0); r.setEnd(spans[k0 + 3].firstChild, 1);
  getSelection().removeAllRanges(); getSelection().addRange(r);
  const a0 = sup.size;
  dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true })); await att(200); const a1 = sup.size;
  dispatchEvent(new KeyboardEvent('keydown', { key: 'z', metaKey: true, bubbles: true })); await att(200); const a2 = sup.size;
  dispatchEvent(new KeyboardEvent('keydown', { key: 'z', metaKey: true, shiftKey: true, bubbles: true })); await att(200); const a3 = sup.size;
  dispatchEvent(new KeyboardEvent('keydown', { key: 'z', metaKey: true, bubbles: true })); await att(200);
  L(`${a1 > a0 && a2 === a0 && a3 === a1 ? 'OK' : 'ÉCHEC'} coupes : ${a0} → ⌫ ${a1} → ⌘Z ${a2} → ⇧⌘Z ${a3}`);
  if (window.__suite) await window.__suite(L, att);
  L('fin');
})().catch(e => __log.push('ERR ' + e + ' ' + e.stack));
true;
