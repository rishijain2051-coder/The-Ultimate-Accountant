/* The opening, once per launch of the app (a reload skips it): the two bars of
   the mark come in from either side and agree, the lower one ticks itself, and
   the maroon field folds up into the cover band while the page comes in.
   A classic script at the end of the page, before app.js; a reload is hidden
   by the class the head's one-liner sets. app.js calls acLaunch.done() when the
   first page is drawn. Only transform and opacity are animated, on layers of
   their own, so the motion stays smooth while app.js builds the page. */
(() => {
  const el = document.getElementById('launch');
  // ?opening: chosen in Ultimate Accountant (app/home.html, or the bridge's Switch link) - play it
  // again, then drop the word, so a reload of the page does not.
  const chosen = /[?&]opening\b/.test(location.search);
  if (chosen) history.replaceState(null, '', location.pathname + location.hash);
  const seen = !chosen && (() => { try { return sessionStorage.getItem('ac-launched'); } catch { return null; } })();
  if (!el || seen || !el.animate) { el?.remove(); window.acLaunch = { done() {} }; return; }
  try { sessionStorage.setItem('ac-launched', '1'); } catch { /* the opening plays again on reload */ }

  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = s => el.querySelector(s);
  const lockup = $('.launch-lockup'), mark = $('.launch-mark');

  const name = $('.launch-name');
  name.innerHTML = [...name.textContent].map(c => `<i>${c === ' ' ? '&nbsp;' : c}</i>`).join('');
  const letters = [...name.children];

  // How much larger than the cover's wordmark (about 207 px wide) to draw it,
  // and where the ruling runs through the two bars.
  const size = () => {
    el.style.setProperty('--s', Math.max(1.6, Math.min(4, innerWidth * 0.6 / 207)).toFixed(3));
    const m = mark.getBoundingClientRect(), [r1, r2] = el.querySelectorAll('.launch-rule');
    r1.style.top = `${m.top + m.height * 0.237}px`;       // the bars' middles, in the viewBox's 190 units
    r2.style.top = `${m.top + m.height * 0.584}px`;
  };
  size();
  addEventListener('resize', size);

  // A damped spring, written out as a linear() easing.
  const spring = (zeta, turns, n = 48) => {
    const w = 2 * Math.PI * turns, wd = w * Math.sqrt(1 - zeta * zeta), pts = [];
    for (let i = 0; i < n; i++) {
      const t = i / n;
      pts.push((1 - Math.exp(-zeta * w * t) * (Math.cos(wd * t) + zeta * w / wd * Math.sin(wd * t))).toFixed(4));
    }
    return `linear(${pts.join(',')},1)`;
  };
  const OUT = 'cubic-bezier(.16,1,.3,1)', SWAP = 'cubic-bezier(.76,0,.24,1)';

  let ready = false, introDone = still, leaving = false;
  const intro = [];
  const go = (node, frames, opts) => intro.push(node.animate(frames, { fill: 'both', ...opts }));

  if (!still) {
    const bar = spring(0.72, 1.1);
    go($('.launch-lines'), [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: 1300, easing: OUT });
    go($('.launch-margin'), [{ transform: 'scaleY(0)' }, { transform: 'scaleY(1)' }], { duration: 1100, delay: 150, easing: OUT });
    go($('.lm-top'), [{ transform: 'translateX(-65vw) scaleX(1.6)', opacity: 0 }, { opacity: 1, offset: 0.2 },
      { transform: 'none', opacity: 1 }], { duration: 950, delay: 150, easing: bar });
    for (const p of [$('.lm-low'), $('.lm-tick')]) {
      go(p, [{ translate: '65vw 0', opacity: 0 }, { opacity: 1, offset: 0.2 }, { translate: '0 0', opacity: 1 }],
        { duration: 950, delay: 230, easing: bar });
    }
    // The lower bar ticks itself: the stroke that came in lying flat stands up.
    go($('.lm-tick'), [{ rotate: '45deg' }, { rotate: '0deg' }], { duration: 700, delay: 950, easing: spring(0.45, 1.3) });
    // Where the bars meet, the lines run on across the page, like ruling.
    for (const r of el.querySelectorAll('.launch-rule')) {
      go(r, [{ transform: 'scaleX(0)', opacity: 1 }, { transform: 'scaleX(1)', opacity: 0 }],
        { duration: 1000, delay: 820, easing: OUT });
    }
    go(mark, [{ transform: 'none' }, { transform: 'scale(.94)', offset: 0.3 }, { transform: 'none' }],
      { duration: 380, delay: 860, easing: 'ease-out' });
    go($('.lm-shine span'), [{ transform: 'translateX(-120%)' }, { transform: 'translateX(260%)' }],
      { duration: 800, delay: 1450, easing: 'cubic-bezier(.4,0,.2,1)' });
    letters.forEach((l, i) => go(l, [{ opacity: 0, transform: 'translateY(.5em)' }, { opacity: 1, transform: 'none' }],
      { duration: 560, delay: 1150 + i * 34, easing: OUT }));
    Promise.all(intro.map(a => a.finished)).then(() => { introDone = true; leave(); });
  }

  // Still waiting for the page after the opening has played: say so, quietly.
  let slow;
  Promise.all(intro.map(a => a.finished)).then(() => { slow = setTimeout(() => el.classList.add('slow'), 1200); });
  setTimeout(() => { ready = true; leave(); }, 12000);        // never stand in front of the app for good
  const hurry = () => intro.forEach(a => a.finish());
  el.addEventListener('pointerdown', hurry);
  addEventListener('keydown', e => { if (['Escape', 'Enter', ' '].includes(e.key)) hurry(); }, { once: true });

  function leave() {
    if (!ready || !introDone || leaving) return;
    leaving = true; clearTimeout(slow); el.classList.remove('slow');
    removeEventListener('resize', size);
    const end = () => el.remove();
    if (still) {
      el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 260, fill: 'both' }).finished.then(end);
      return;
    }
    // Measured now, not at the start: the window has its final size by this time.
    const home = document.querySelector('.cover .wordmark').getBoundingClientRect();
    const from = lockup.getBoundingClientRect(), k = home.width / from.width;
    const fold = 900;
    lockup.style.transformOrigin = '0 0';
    lockup.animate([{ transform: 'none' }, { transform: `translate(${home.left - from.left}px,${home.top - from.top}px) scale(${k})` }],
      { duration: fold, easing: SWAP, fill: 'both' });
    for (const n of el.querySelectorAll('.launch-glow, .launch-lines, .launch-margin')) {
      n.animate([{ opacity: 1 }, { opacity: 0 }], { duration: fold * 0.45, fill: 'both' });
    }
    const band = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--cover-h')) || 56;
    const field = $('.launch-field').animate([{ transform: 'none' }, { transform: `scaleY(${band / innerHeight})` }],
      { duration: fold, easing: SWAP, fill: 'both' });
    const page = [document.getElementById('main'), document.querySelector('.maker')].filter(Boolean);
    page.forEach((p, i) => p.animate([{ opacity: 0, transform: 'translateY(40px)' }, { opacity: 1, transform: 'none' }],
      { duration: 750, delay: fold * 0.5 + i * 90, easing: OUT, fill: 'backwards' }));
    // The band is the cover itself now; letting it go shows the cover's buttons.
    field.finished.then(() => el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 260, fill: 'both' }).finished).then(end);
  }

  window.acLaunch = { done() { ready = true; leave(); } };
})();
