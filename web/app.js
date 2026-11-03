const app = document.querySelector('#app');
const icons = {
  book: '<path d="M3 5c4-1 6 0 9 2 3-2 5-3 9-2v14c-4-1-6 0-9 2-3-2-5-3-9-2V5Zm9 2v14"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="3"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v3"/>',
  star: '<path d="m12 3 2.7 5.7 6.3.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.3-.9L12 3Z"/>',
  flame: '<path d="M13 3c1 5-4 5-2 9 2-1 3-3 3-4 4 4 6 7 3 11a7 7 0 0 1-11-8c1-2 3-3 7-8Z"/>',
  home: '<path d="m3 10 9-7 9 7v11h-6v-7H9v7H3V10Z"/>',
  chart: '<path d="M4 20h17M7 16v-5m5 5V5m5 11V8"/>',
  settings: '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 3h.01"/>',
  voice: '<path d="m11 4-6 5H2v6h3l6 5V4Zm4 4a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
  mic: '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-4 0h8"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z"/>',
  logout: '<path d="M9 4H4v16h5m4-4 4-4-4-4m-4 4h12"/>',
  download: '<path d="M12 3v12m-4-4 4 4 4-4M4 17v4h16v-4"/>',
  repeat: '<path d="M20 7H7a4 4 0 0 0-4 4m1-5-1 5 5-1m-4 7h13a4 4 0 0 0 4-4m-1 5 1-5-5 1"/>',
  heart: '<path d="M20 5c-3-3-6-1-8 1-2-2-5-4-8-1-6 6 8 15 8 15S26 11 20 5Z"/>'
};
const icon = (name, cls = '') => `<svg class="icon ${cls}" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${icons[name] || icons.book}</svg>`;
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
let state = {session:null, curriculum:null, lesson:null, phase:'cards', index:0, answers:[], correct:false, feedback:null, micConsent:false};
let lessonVisit = 0;
let reviewVisit = 0;
let dashboardReview = null;
let recognition = null;
let installPrompt = null;
let busy = false;
let toastTimer;
try { document.documentElement.dataset.theme = localStorage.getItem('academy-theme') || 'light'; } catch {}
function toast(message) {
  clearTimeout(toastTimer);
  const node = document.querySelector('#toast');
  node.textContent = message; node.hidden = false;
  toastTimer = setTimeout(() => { node.hidden = true; }, 5500);
}
async function api(path, {method = 'GET', body} = {}) {
  const headers = {'Accept':'application/json'};
  if (body) headers['Content-Type'] = 'application/json';
  const response = await fetch(`/api/${path}`, {method,headers,credentials:'same-origin',cache:'no-store',body:body ? JSON.stringify({...body,csrfToken:state.session?.csrfToken}) : undefined});
  let data;
  try { data = await response.json(); } catch { throw new Error('Ο διακομιστής δεν απάντησε σωστά. Δοκίμασε ξανά.'); }
  if (!response.ok) throw new Error(data.error || 'Κάτι δεν πήγε καλά. Δοκίμασε ξανά.');
  return data;
}
async function refresh() { state.session = await api('session'); dashboardReview = null; }
function go(route) { if (location.hash === `#${route}`) render(); else location.hash = route; }
function stopVoice() {
  if (recognition) { const active = recognition; recognition = null; active.onend = null; active.onresult = null; active.onerror = null; active.abort(); }
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
}
function navItem(route, label, glyph) { return `<a href="#${route}" class="nav-link ${location.hash === `#${route}` ? 'active' : ''}">${icon(glyph)}<span>${label}</span></a>`; }
function shell(content, {lesson = false} = {}) {
  const user = state.session?.user;
  const header = `<a class="brand" href="#${user ? 'dashboard' : 'home'}"><img src="/assets/logo.svg" alt="" width="40" height="40"><span>AI Language<span class="brand-second">Academy<span class="brand-dot">.</span></span></span></a>`;
  if (!user) return `<header class="public-header wrap">${header}<nav aria-label="Κύρια πλοήγηση"><a class="text-link hide-small" href="#help">Πώς λειτουργεί</a><a class="button button-small button-outline" href="#login">Σύνδεση ${icon('arrow')}</a></nav></header>${content}<footer class="public-footer wrap"><span>Μια νέα γλώσσα. Ένας νέος κόσμος.</span><a href="#help">Σχετικά & απόρρητο</a><span>Δοκιμαστική έκδοση · 18+</span></footer>`;
  return `<div class="workspace"><aside class="sidebar">${header}<span class="sidebar-label">Ο ΔΙΚΟΣ ΣΟΥ ΧΩΡΟΣ</span><nav aria-label="Κύρια πλοήγηση">${navItem('dashboard','Η διαδρομή μου','home')}${navItem('review','Επαναλήψεις','repeat')}${navItem('progress','Η πρόοδός μου','chart')}${navItem('settings','Ρυθμίσεις','settings')}${navItem('help','Βοήθεια','help')}</nav><div class="sidebar-note">${icon('heart')}<p>Κάθε μικρό βήμα<br>σε πάει πιο μακριά.</p></div><button class="user-block" data-action="logout" aria-label="Αποσύνδεση"><span class="avatar">${escape(user.name.slice(0,1).toUpperCase())}</span><span>${escape(user.name)}<small>Δοκιμαστικός λογαριασμός</small></span>${icon('logout')}</button></aside><div class="workspace-main"><header class="app-header"><a class="mobile-brand" href="#dashboard"><img src="/assets/logo.svg" alt="Academy" width="34" height="34"></a><span class="course-pill"><span aria-hidden="true">🇬🇧</span> Αγγλικά <span class="level-label">A1</span></span><div class="header-metrics"><span title="Πόντοι εμπειρίας">${icon('star')} ${state.session.progress?.xp || 0} <span class="hide-small">XP</span></span><span title="Συνεχόμενες ημέρες μαθημάτων">${icon('flame')} ${state.session.progress?.streak || 0}</span><button class="mobile-logout round-button" data-action="logout" aria-label="Αποσύνδεση">${icon('logout')}</button></div></header>${content}</div><nav class="mobile-nav" aria-label="Πλοήγηση κινητού">${navItem('dashboard','Διαδρομή','home')}${navItem('review','Επανάληψη','repeat')}${navItem('progress','Πρόοδος','chart')}${navItem('settings','Ρυθμίσεις','settings')}${navItem('help','Βοήθεια','help')}</nav></div>`;
}
function landing() {
  return shell(`<main id="main-content" class="landing wrap" tabindex="-1"><section class="hero"><div class="hero-copy"><span class="eyebrow"><span class="tiny-dot"></span> Η ΝΕΑ ΣΟΥ ΑΡΧΗ ΣΤΑ ΑΓΓΛΙΚΑ</span><h1>Μια νέα γλώσσα.<br>Ένας κόσμος<br><em>δυνατοτήτων.</em></h1><p class="hero-description">Μάθε αγγλικά με μικρά, ξεκάθαρα μαθήματα και έναν διαδραστικό πίνακα. Στον δικό σου ρυθμό, από την πρώτη σου λέξη.</p><a class="button button-primary button-large" href="#register">Ας κάνουμε την αρχή ${icon('arrow')}</a><p class="quiet hero-footnote">Χωρίς κάρτα · Δωρεάν δοκιμαστική εφαρμογή · Για ενήλικες</p><div class="hero-trust"><span class="mini-avatars"><b>α</b><b>β</b><b>γ</b></span><span><strong>Από το «δεν ξέρω» στο «μπορώ».</strong><br>Ελληνικές εξηγήσεις. Αληθινά μικρά βήματα.</span></div></div><div class="hero-visual" aria-label="Παράδειγμα εκπαιδευτικού πίνακα"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><span class="floating-label label-top">${icon('sun')} Μια λέξη τη φορά.</span><div class="demo-board"><div class="board-topline"><span class="tiny-dot"></span> Ο ΠΙΝΑΚΑΣ ΣΟΥ <span>01 / 03</span></div><img class="hero-car" src="/assets/car.svg" alt="Πρωτότυπη εικονογράφηση αυτοκινήτου"><div class="demo-word">car <button class="round-button" data-action="speak-demo" aria-label="Άκου τη λέξη car">${icon('voice')}</button></div><span class="demo-translation">αυτοκίνητο</span><div class="demo-example"><span>This is a car.</span><small>Αυτό είναι ένα αυτοκίνητο.</small></div></div><div class="floating-label label-bottom"><span class="success-bubble">${icon('check')}</span><span><strong>Η πρώτη σου λέξη!</strong><small>Κάθε αρχή μετράει.</small></span></div><span class="decor-star" aria-hidden="true">✳</span></div></section><section class="landing-benefits" aria-label="Ο τρόπος μας"><div><span class="feature-icon">${icon('clock')}</span><span><strong>5 λεπτά αρκούν για αρχή</strong><small>Μικρά μαθήματα, χωρίς βιασύνη.</small></span></div><div><span class="feature-icon peach">${icon('book')}</span><span><strong>Βλέπεις. Ακούς. Δοκιμάζεις.</strong><small>Ένας πίνακας γεμάτος ανακαλύψεις.</small></span></div><div><span class="feature-icon yellow">${icon('globe')}</span><span><strong>Από τα ελληνικά στα αγγλικά</strong><small>Εξηγήσεις που καταλαβαίνεις.</small></span></div></section><section class="honesty-note"><strong>Μια εφαρμογή που μεγαλώνει μαζί μας.</strong> Η πρώτη έκδοση περιλαμβάνει 3 μαθήματα A1 και δοκιμαστικό καθηγητή με προκαθορισμένες εξηγήσεις. Ο ελεύθερος διάλογος με AI θα προστεθεί σε επόμενο στάδιο.</section></main>`);
}
function auth(mode) {
  const register = mode === 'register';
  return shell(`<main id="main-content" class="auth-page wrap" tabindex="-1"><section class="auth-aside"><span class="eyebrow">ΕΝΑ ΜΙΚΡΟ ΒΗΜΑ ΓΙΑ ΣΕΝΑ</span><h1>Η επόμενη<br>ιστορία σου<br><em>ξεκινά εδώ.</em></h1><p>Δεν χρειάζεται να ξέρεις ήδη αγγλικά.<br>Αρκεί να θέλεις να κάνεις την αρχή.</p><div class="auth-art" aria-hidden="true">Aa<span>Hello!</span><i>Γεια σου!</i></div></section><section class="form-card"><span class="eyebrow">${register ? 'ΚΑΛΩΣ ΗΡΘΕΣ' : 'ΚΑΛΩΣ ΕΠΕΣΤΡΕΨΕΣ'}</span><h2>${register ? 'Ας γνωριστούμε.' : 'Συνέχισε τη διαδρομή σου.'}</h2><p class="quiet">${register ? 'Δημιούργησε τον δοκιμαστικό σου λογαριασμό.' : 'Συνδέσου στον λογαριασμό σου.'}</p><form id="auth-form" data-mode="${mode}">${register ? '<label>Πώς σε λένε;<input name="name" autocomplete="given-name" required minlength="2" maxlength="60" placeholder="Το όνομά σου"></label>' : ''}<label>Email<input type="email" name="email" autocomplete="email" required maxlength="254" placeholder="onoma@example.com"></label><label>Κωδικός<input type="password" name="password" autocomplete="${register ? 'new-password' : 'current-password'}" required minlength="${register ? '10' : '1'}" maxlength="128" placeholder="${register ? 'Τουλάχιστον 10 χαρακτήρες' : 'Ο κωδικός σου'}"></label>${register ? '<label class="checkbox-label"><input type="checkbox" name="ageConfirmed" required><span>Είμαι 18 ετών ή μεγαλύτερος/η και διάβασα την <a href="#help">ενημέρωση απορρήτου</a>.</span></label>' : ''}<p id="form-error" class="form-error" role="alert" hidden></p><button class="button button-primary button-full" type="submit">${register ? 'Δημιουργία λογαριασμού' : 'Σύνδεση'} ${icon('arrow')}</button></form><p class="auth-switch">${register ? 'Έχεις ήδη λογαριασμό; <a href="#login">Συνδέσου</a>' : 'Πρώτη φορά εδώ; <a href="#register">Κάνε την αρχή</a>'}</p><p class="small-note">Δοκιμαστική έκδοση: δεν αποστέλλονται email επιβεβαίωσης ή ανάκτησης κωδικού. Χρησιμοποίησε έναν μοναδικό κωδικό και κράτησέ τον ασφαλή.</p></section></main>`);
}
function onboarding() {
  return shell(`<main id="main-content" class="page onboarding-page" tabindex="-1"><div class="page-intro"><span class="eyebrow">ΠΡΙΝ ΚΑΝΟΥΜΕ ΤΗΝ ΑΡΧΗ</span><h1>Μια διαδρομή στα μέτρα σου.</h1><p>Λίγα πράγματα για σένα, για να ξεκινήσουμε σωστά.</p></div><form id="onboarding-form" class="form-card wide-form"><div class="two-columns"><label>Η γλώσσα μου<select name="nativeLanguage"><option value="el">Ελληνικά</option></select></label><label>Θέλω να μάθω<select name="targetLanguage"><option value="en">Αγγλικά</option></select></label></div><label>Γιατί θέλεις να μάθεις αγγλικά;<select name="reason"><option value="travel">Για ταξίδια και νέες εμπειρίες</option><option value="work">Για τη δουλειά μου</option><option value="study">Για σπουδές</option><option value="personal">Για εμένα και την καθημερινότητά μου</option></select></label><fieldset><legend>Πόσο χρόνο θα ήθελες κάθε μέρα;</legend><div class="goal-options"><label><input type="radio" name="goalMinutes" value="5"><span>5 λεπτά<small>Μια μικρή αρχή</small></span></label><label><input type="radio" name="goalMinutes" value="10" checked><span>10 λεπτά<small>Ένας όμορφος ρυθμός</small></span></label><label><input type="radio" name="goalMinutes" value="15"><span>15 λεπτά<small>Ένα βήμα παραπάνω</small></span></label></div></fieldset><div class="info-box">${icon('book')}<p><strong>Ξεκινάμε από την αρχή, στο A1.</strong> Η δοκιμαστική έκδοση έχει τρία εισαγωγικά μαθήματα. Το τεστ κατάταξης και οι υπόλοιπες γλώσσες θα ακολουθήσουν.</p></div><p id="form-error" class="form-error" role="alert" hidden></p><button class="button button-primary" type="submit">Στη δική μου διαδρομή ${icon('arrow')}</button></form></main>`);
}
function dashboard() {
  const progress = state.session.progress;
  const completed = progress.completedLessons || [];
  const lessons = state.curriculum.lessons;
  const next = lessons.find(lesson => !completed.includes(lesson.id));
  loadDashboardReview();
  return shell(`<main id="main-content" class="page dashboard-page" tabindex="-1"><div class="dashboard-heading"><div><span class="eyebrow">ΚΑΘΕ ΜΕΡΑ, ΛΙΓΟ ΠΙΟ ΚΟΝΤΑ</span><h1>Γεια σου, ${escape(state.session.user.name.split(' ')[0])}<span class="greeting-spark" aria-hidden="true">✳</span></h1><p>Η καλύτερη στιγμή για ένα μικρό βήμα είναι τώρα.</p></div><div class="daily-badge">${icon('sun')}<span>Ο στόχος σου<strong>${state.session.profile.goalMinutes} λεπτά / ημέρα</strong></span></div></div><div class="dashboard-columns"><div class="journey-column"><section class="continue-card"><span class="eyebrow">${next ? 'ΤΟ ΕΠΟΜΕΝΟ ΜΙΚΡΟ ΣΟΥ ΒΗΜΑ' : 'ΜΙΑ ΟΜΟΡΦΗ ΑΡΧΗ ΟΛΟΚΛΗΡΩΘΗΚΕ'}</span><h2>${next ? escape(next.title) : 'Τα κατάφερες και στα 3 μαθήματα!'}</h2><p>${next ? escape(next.subtitle) : 'Μπορείς να επαναλάβεις όποιο μάθημα θέλεις. Νέες ενότητες θα προστεθούν αργότερα.'}</p><a class="button button-cream" href="#lesson/${next ? next.id : lessons[0].id}">${next ? 'Συνέχεια μαθήματος' : 'Κάνε μια επανάληψη'} ${icon('arrow')}</a><span class="continue-decor" aria-hidden="true">Aa</span></section><section class="journey-section"><div class="section-heading"><div><span class="eyebrow">ΕΠΙΠΕΔΟ A1 · ΕΝΟΤΗΤΑ 01</span><h2>Οι βάσεις για κάθε αρχή.</h2></div><span class="count-pill">${completed.length} / ${lessons.length}</span></div><div class="lesson-path">${lessons.map((lesson,index) => {
    const done = completed.includes(lesson.id);
    const unlocked = index === 0 || completed.includes(lessons[index-1].id);
    return `<article class="path-row ${done ? 'completed' : unlocked ? 'current' : 'locked'}"><span class="path-node">${done ? icon('check') : unlocked ? icon('book') : icon('lock')}</span><div class="path-card"><div class="path-meta">ΜΑΘΗΜΑ ${String(index+1).padStart(2,'0')} ${done ? '<span>Ολοκληρώθηκε</span>' : !unlocked ? '<span>Ανοίγει μετά το προηγούμενο</span>' : '<span>Η σειρά σου!</span>'}</div><h3>${escape(lesson.title)}</h3><p>${escape(lesson.subtitle)}</p><div class="path-bottom"><span>${icon('clock')} ${lesson.durationMinutes} λεπτά <span class="path-xp">· ${lesson.xp} XP</span></span>${unlocked ? `<a class="${done ? 'text-link' : 'button button-small button-primary'}" href="#lesson/${lesson.id}">${done ? 'Επανάληψη' : 'Ξεκίνα'} ${icon('arrow')}</a>` : '<span class="locked-label">Κλειδωμένο</span>'}</div></div></article>`;
  }).join('')}</div></section></div><aside class="dashboard-side">${dashboardReviewCard()}<section class="side-card progress-mini"><div class="section-heading"><h3>Η αρχή σου σε αριθμούς</h3>${icon('chart')}</div><div class="progress-ring"><strong>${completed.length}<small>από ${lessons.length} μαθήματα</small></strong></div><div class="mini-stat-row"><span>Πόντοι εμπειρίας</span><strong>${progress.xp} XP</strong></div><div class="mini-stat-row"><span>Συνεχόμενες ημέρες</span><strong>${progress.streak}</strong></div><a class="text-link" href="#progress">Δες την πρόοδό σου ${icon('arrow')}</a></section><section class="side-card tip-card"><span class="tip-symbol" aria-hidden="true">“</span><h3>Δεν χρειάζεται να<br>είσαι τέλειος.</h3><p>Χρειάζεται μόνο να επιστρέφεις. Τα λάθη είναι μέρος της διαδρομής.</p><span class="eyebrow">ΜΙΑ ΜΙΚΡΗ ΥΠΕΝΘΥΜΙΣΗ</span></section><p class="side-disclaimer">Δοκιμαστική διαδρομή · 3 μαθήματα A1<br>Η ολοκλήρωση δεν πιστοποιεί επίπεδο γλωσσομάθειας.</p></aside></div></main>`);
}
let pendingReviewAttempt = null;
const athensDate = value => {
  if (!value) return '—';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '—';
  return new Intl.DateTimeFormat('el-GR',{dateStyle:'medium',timeStyle:'short',timeZone:'Europe/Athens'}).format(date);
};
function learningContext(id, visit, userId) {
  return lessonVisit === visit && state.lesson?.id === id && state.session?.user?.id === userId && location.hash === `#lesson/${id}`;
}
const pendingLessonSaves = new Map();
function loadingLesson() {
  return shell(`<main id="main-content" class="page lesson-loading" tabindex="-1"><a class="text-link" href="#dashboard">← Πίσω στη διαδρομή</a><div class="learning-loader"><span class="feature-icon">${icon('book')}</span><h1>${state.checkpointError ? 'Η θέση σου δεν φορτώθηκε ακόμη.' : 'Βρίσκουμε το σημείο που έμεινες…'}</h1><p role="status">${state.checkpointError ? escape(state.checkpointError) : 'Ένα μικρό λεπτό, για να συνεχίσουμε από τη σωστή θέση.'}</p>${state.checkpointError ? '<button class="button button-primary" data-action="lesson-retry">Νέα προσπάθεια φόρτωσης</button>' : ''}</div></main>`);
}
async function loadLessonCheckpoint(id, visit) {
  const userId = state.session.user.id;
  try {
    const pending = pendingLessonSaves.get(`${userId}:${id}`);
    if (pending) await pending.catch(()=>{});
    if (!learningContext(id,visit,userId)) return;
    const data = await api(`lesson-state?lessonId=${encodeURIComponent(id)}`);
    if (!learningContext(id,visit,userId)) return;
    const checkpoint = data.checkpoint;
    if (checkpoint) {
      Object.assign(state,{phase:checkpoint.phase,index:checkpoint.index,answers:[...checkpoint.answers],correct:checkpoint.correct,resumed:true});
      if (checkpoint.correct) state.feedback = {kind:'success',title:'Η σωστή απάντησή σου έχει αποθηκευτεί.',text:state.lesson.exercises[checkpoint.index].explanation};
    }
    state.checkpointLoaded = true;
    state.checkpointError = null;
    render();
  } catch(error) {
    if (!learningContext(id,visit,userId)) return;
    state.checkpointError = 'Δεν μπορέσαμε να διαβάσουμε την αποθηκευμένη θέση σου. Έλεγξε τη σύνδεση και δοκίμασε ξανά.';
    render();
  }
}
async function saveLessonTransition(patch) {
  const id = state.lesson.id, visit = lessonVisit, userId = state.session.user.id;
  const target = {lessonId:id,phase:state.phase,index:state.index,answers:[...state.answers],correct:state.correct,...patch};
  const status = document.querySelector('#lesson-save-status');
  if (status) status.textContent = 'Αποθηκεύεται η θέση σου…';
  const key = `${userId}:${id}`;
  const request = api('lesson-state',{method:'PUT',body:target});
  pendingLessonSaves.set(key,request);
  try {
    await request;
    if (!learningContext(id,visit,userId)) return false;
    Object.assign(state,{phase:target.phase,index:target.index,answers:[...target.answers],correct:target.correct,saved:true,feedback:target.correct ? {kind:'success',title:'Σωστά! Ένα ακόμη μικρό βήμα.',text:state.lesson.exercises[target.index].explanation} : null,resumed:false});
    render();
    return true;
  } catch(error) {
    if (learningContext(id,visit,userId)) {
      const message = 'Η αλλαγή δεν επιβεβαιώθηκε. Δεν προχωρήσαμε στο επόμενο βήμα. Κράτησε τη σελίδα ανοιχτή και πάτησε ξανά το ίδιο κουμπί για αποθήκευση.';
      const currentStatus = document.querySelector('#lesson-save-status');
      if (currentStatus) { currentStatus.textContent = message; currentStatus.classList.add('save-error'); }
      toast(message);
    }
    return false;
  } finally {
    if (pendingLessonSaves.get(key) === request) pendingLessonSaves.delete(key);
  }
}
function loadDashboardReview() {
  const userId = state.session.user.id;
  if (dashboardReview?.userId === userId) return;
  const entry = {userId,loading:true,data:null,error:false};
  dashboardReview = entry;
  api('review').then(data => {
    if (dashboardReview !== entry || state.session.user?.id !== userId) return;
    Object.assign(entry,{loading:false,data});
    if (location.hash === '#dashboard') render();
  }).catch(()=>{
    if (dashboardReview !== entry || state.session.user?.id !== userId) return;
    Object.assign(entry,{loading:false,error:true});
    if (location.hash === '#dashboard') render();
  });
}
function dashboardReviewCard() {
  const entry = dashboardReview;
  const count = entry?.data?.dueCount;
  return `<section class="side-card review-promo"><span class="feature-icon">${icon('repeat')}</span><span class="eyebrow">ΛΙΓΗ ΕΠΑΝΑΛΗΨΗ, ΚΑΘΕ ΤΟΣΟ</span><h3>${typeof count === 'number' ? count > 0 ? `${count} στοιχεία σε περιμένουν.` : 'Η εξάσκηση έχει τον ρυθμό της.' : 'Η μικρή σου επανάληψη.'}</h3><p>${entry?.loading ? 'Φορτώνουμε το πρόγραμμα επαναλήψεων…' : entry?.error ? 'Το πρόγραμμα δεν φορτώθηκε. Μπορείς να δοκιμάσεις ξανά στις Επαναλήψεις.' : count > 0 ? 'Επέστρεψε σε λέξεις και φράσεις που ήδη συνάντησες.' : 'Τα νέα στοιχεία επιστρέφουν για επανάληψη από την επόμενη ημέρα. Μπορείς να εξασκηθείς και νωρίτερα.'}</p><a class="text-link" href="#review">Στις επαναλήψεις μου ${icon('arrow')}</a></section>`;
}
function reviewMatches(visit,userId) { return reviewVisit === visit && state.review?.visit === visit && state.session.user?.id === userId && location.hash === '#review'; }
function beginReviewLoad() {
  const visit = ++reviewVisit, userId = state.session.user.id;
  state.review = {visit,loading:true,error:null,items:[],queue:[],index:0,mode:'due',result:null,pending:null,answer:'',completed:0};
  api('review').then(data => {
    if (!reviewMatches(visit,userId)) return;
    const review = state.review;
    Object.assign(review,{loading:false,items:data.items,dueCount:data.dueCount,queue:data.items.filter(item=>item.isDue)});
    if (pendingReviewAttempt?.userId === userId) {
      const index = data.items.findIndex(item=>item.lessonId === pendingReviewAttempt.lessonId && item.exerciseId === pendingReviewAttempt.exerciseId);
      if (index >= 0) Object.assign(review,{mode:'all',queue:[...data.items],index,pending:pendingReviewAttempt,answer:pendingReviewAttempt.answer,error:'Η προηγούμενη προσπάθεια δεν επιβεβαιώθηκε. Επανάλαβε την αποστολή για να διαβάσουμε το ίδιο αποτέλεσμα, χωρίς διπλή καταχώριση.'});
    }
    render();
  }).catch(()=>{
    if (!reviewMatches(visit,userId)) return;
    Object.assign(state.review,{loading:false,error:'Δεν μπορέσαμε να φορτώσουμε τις επαναλήψεις σου. Έλεγξε τη σύνδεση και δοκίμασε ξανά.'});
    render();
  });
}
function reviewView() {
  if (!state.review) beginReviewLoad();
  const review = state.review;
  const item = review.queue[review.index];
  const intro = `<div class="page-intro"><span class="eyebrow">ΞΑΝΑΒΡΙΣΚΟΥΜΕ ΟΣΑ ΜΑΘΑΜΕ</span><h1>Μια μικρή επανάληψη.</h1><p>Λέξεις και φράσεις από τα ολοκληρωμένα μαθήματά σου, στον δικό σου ρυθμό.</p></div>`;
  if (review.loading) return shell(`<main id="main-content" class="page review-page" tabindex="-1">${intro}<div class="side-card learning-loader"><p role="status">Φορτώνουμε τις επαναλήψεις σου…</p></div></main>`);
  if (review.error && !review.items.length) return shell(`<main id="main-content" class="page review-page" tabindex="-1">${intro}<section class="side-card"><p id="review-error" class="form-error" role="alert">${escape(review.error)}</p><button class="button button-primary" data-action="review-reload">Νέα προσπάθεια φόρτωσης</button></section></main>`);
  const filters = `<div class="review-filters" aria-label="Επιλογή υλικού"><button class="button button-small ${review.mode === 'due' ? 'button-primary' : 'button-outline'}" data-action="review-start-due" ${review.pending ? 'disabled' : ''} aria-pressed="${review.mode === 'due'}">Για τώρα <span class="review-count">${review.dueCount}</span></button><button class="button button-small ${review.mode === 'all' ? 'button-primary' : 'button-outline'}" data-action="review-start-all" ${review.pending ? 'disabled' : ''} aria-pressed="${review.mode === 'all'}">Εξάσκηση όλων <span class="review-count">${review.items.length}</span></button></div>`;
  const note = `<div class="review-explainer"><p>Οι σωστές απαντήσεις στην ώρα τους μεταφέρουν σταδιακά την επανάληψη σε 1, 3, 7, 14 και 30 ημέρες. Αν δυσκολευτείς, το στοιχείο επιστρέφει νωρίτερα. Η προαιρετική σωστή εξάσκηση πριν από την ώρα της δεν αλλάζει την ημερομηνία. Δεν προστίθενται XP.</p><p>Το πρόγραμμα βοηθά την εξάσκηση· δεν αποτελεί μέτρηση απομνημόνευσης ή πιστοποίηση επιπέδου. Οι ώρες εμφανίζονται σε ώρα Ελλάδας.</p></div>`;
  let body;
  if (!review.items.length) body = `<section class="side-card empty-state"><span class="feature-icon">${icon('book')}</span><h2>Η συλλογή σου ξεκινά με ένα μάθημα.</h2><p>Όταν ολοκληρώσεις το πρώτο μάθημα, οι ασκήσεις του θα εμφανιστούν εδώ για επανάληψη από την επόμενη ημέρα.</p><a class="button button-primary" href="#dashboard">Στη διαδρομή μου ${icon('arrow')}</a></section>`;
  else if (!item) body = `<section class="side-card empty-state"><span class="feature-icon">${icon(review.completed ? 'check' : 'sun')}</span><h2>${review.completed ? `Έκανες εξάσκηση σε ${review.completed} στοιχεία.` : 'Δεν έχεις επαναλήψεις για τώρα.'}</h2><p>${review.completed ? 'Η επόμενη επανάληψη έχει προγραμματιστεί για κάθε στοιχείο. Μπορείς να επιστρέψεις στη διαδρομή σου.' : 'Το πρόγραμμά σου θα φέρει τις λέξεις πίσω στην κατάλληλη στιγμή. Μπορείς, αν θέλεις, να εξασκηθείς και νωρίτερα.'}</p><button class="button button-primary" data-action="review-start-all">Εξάσκηση όλων ${icon('repeat')}</button><a class="text-link" href="#dashboard">Στη διαδρομή μου ${icon('arrow')}</a></section>`;
  else body = `<div class="review-classroom"><section class="whiteboard review-board"><div class="board-topline"><span class="tiny-dot"></span> ΜΙΚΡΗ ΕΠΑΝΑΛΗΨΗ <span>${review.index+1} / ${review.queue.length}</span></div><div class="exercise-content"><span class="exercise-type">${review.result ? 'ΤΟ ΑΠΟΤΕΛΕΣΜΑ ΤΗΣ ΕΠΑΝΑΛΗΨΗΣ' : item.isDue ? 'Η ΩΡΑ ΤΗΣ ΕΠΑΝΑΛΗΨΗΣ' : 'ΠΡΟΑΙΡΕΤΙΚΗ ΕΞΑΣΚΗΣΗ ΝΩΡΙΤΕΡΑ'}</span><h2>${escape(item.prompt)}</h2><form id="review-form"><label class="answer-label" for="review-answer">Η απάντησή σου στα αγγλικά</label><div class="answer-input-row"><input id="review-answer" name="answer" lang="en" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="160" required value="${escape(review.answer)}" ${review.pending || review.result?.correct ? 'readonly' : ''} placeholder="Γράψε εδώ…"></div><p id="review-error" class="form-error" role="alert" ${review.error ? '' : 'hidden'}>${escape(review.error || '')}</p><div id="review-feedback" class="answer-feedback ${review.result ? review.result.correct ? 'success' : 'error' : ''}" role="status" aria-live="polite" ${review.result ? '' : 'hidden'}>${review.result ? `<strong>${review.result.correct ? 'Σωστά! Έκανες μια χρήσιμη επανάληψη.' : `Ας το ξαναδούμε. Σωστή απάντηση: ${escape(review.result.expectedAnswer)}`}</strong><span>${escape(review.result.explanation)}</span><span>Επόμενη επανάληψη: <strong>${escape(athensDate(review.result.nextDueAt))}</strong> · ώρα Ελλάδας.</span>${review.result.scheduleAdvanced === false && review.result.correct ? '<span>Ήταν προαιρετική εξάσκηση πριν από την ώρα της. Το πρόγραμμα επανάληψης διατηρήθηκε.</span>' : ''}` : ''}</div>${review.pending ? '<button type="button" class="button button-primary button-full" data-action="review-retry">Νέα προσπάθεια αποστολής</button><p class="small-note">Στέλνουμε ξανά την ίδια προσπάθεια. Το αποτέλεσμα καταχωρίζεται μόνο μία φορά.</p>' : !review.result?.correct ? `<button class="button button-primary button-full" type="submit">${review.result ? 'Θέλω να ξαναδοκιμάσω' : 'Έλεγχος απάντησης'} ${icon('check')}</button>` : ''}${review.result ? `<button type="button" class="button ${review.result.correct ? 'button-primary' : 'button-outline'} button-full" data-action="review-next">${review.index === review.queue.length-1 ? 'Ολοκλήρωση επανάληψης' : 'Επόμενο στοιχείο'} ${icon('arrow')}</button>` : ''}</form></div></section><aside class="side-card review-aside"><span class="feature-icon">${icon('repeat')}</span><h3>Λίγη απόσταση βοηθά.</h3><p>Μια λέξη που ξανασυναντάς μετά από λίγο καιρό γίνεται πιο οικεία. Δοκίμασε πριν δεις τη λύση.</p><p class="review-due-label">${item.isDue ? 'Προγραμματισμένη για' : 'Επόμενη επανάληψη'}<strong>${escape(athensDate(item.dueAt))}</strong><span>ώρα Ελλάδας</span></p><button class="button button-outline button-full" data-action="review-hint">Μια μικρή βοήθεια ${icon('help')}</button><p id="review-hint" class="hint-text" hidden>${escape(item.hint)}</p></aside></div>`;
  return shell(`<main id="main-content" class="page review-page" tabindex="-1">${intro}${filters}${body}${note}</main>`);
}
function setReviewMode(mode) {
  const review = state.review;
  if (review.pending) return;
  Object.assign(review,{mode,queue:mode === 'all' ? [...review.items] : review.items.filter(item=>item.isDue),index:0,result:null,error:null,answer:'',completed:0});
  render();
}
async function submitReviewAttempt() {
  const review = state.review;
  const item = review.queue[review.index];
  if (!item) return;
  const visit = review.visit, userId = state.session.user.id;
  if (!review.pending) {
    const answer = String(document.querySelector('#review-answer')?.value || '').trim();
    if (!answer) return;
    const attemptId = window.crypto.randomUUID ? window.crypto.randomUUID() : [...window.crypto.getRandomValues(new Uint8Array(16))].map(n=>n.toString(16).padStart(2,'0')).join('');
    review.pending = {userId,lessonId:item.lessonId,exerciseId:item.exerciseId,answer,attemptId};
    review.answer = answer; review.result = null; review.error = null;
    pendingReviewAttempt = review.pending;
  }
  const pending = review.pending;
  const input = document.querySelector('#review-answer');
  if (input) input.readOnly = true;
  const errorNode = document.querySelector('#review-error');
  if (errorNode) { errorNode.textContent = 'Καταχωρίζουμε την προσπάθειά σου…'; errorNode.hidden = false; errorNode.classList.remove('form-error'); }
  try {
    const result = await api('review',{method:'POST',body:{lessonId:pending.lessonId,exerciseId:pending.exerciseId,answer:pending.answer,attemptId:pending.attemptId}});
    if (pendingReviewAttempt === pending) pendingReviewAttempt = null;
    dashboardReview = null;
    if (!reviewMatches(visit,userId)) return;
    Object.assign(review,{pending:null,result,error:null});
    const source = review.items.find(entry=>entry.lessonId === item.lessonId && entry.exerciseId === item.exerciseId);
    if (source) Object.assign(source,{dueAt:result.nextDueAt,stage:result.stage,isDue:new Date(result.nextDueAt).getTime() <= Date.now()});
    review.dueCount = review.items.filter(entry=>entry.isDue).length;
    render();
  } catch(error) {
    if (!reviewMatches(visit,userId)) return;
    review.error = 'Η προσπάθεια δεν επιβεβαιώθηκε. Κράτησε αυτή τη σελίδα ανοιχτή και δοκίμασε ξανά. Θα ζητήσουμε το ίδιο αποτέλεσμα, χωρίς διπλή καταχώριση.';
    render();
  }
}

function lessonView(id) {
  const lessons = state.curriculum.lessons;
  const lesson = lessons.find(item => item.id === id);
  const pos = lessons.findIndex(item => item.id === id);
  if (!lesson || (pos > 0 && !state.session.progress.completedLessons.includes(lessons[pos-1].id))) return shell(`<main class="page" id="main-content"><h1>Ένα βήμα τη φορά.</h1><p>Αυτό το μάθημα ανοίγει όταν ολοκληρώσεις το προηγούμενο.</p><a class="button button-primary" href="#dashboard">Πίσω στη διαδρομή</a></main>`);
  if (state.lesson?.id !== id) {
    state = {...state,lesson,phase:'cards',index:0,answers:[],correct:false,feedback:null,micConsent:false,resumed:false,saved:false,checkpointLoaded:false,checkpointError:null};
    loadLessonCheckpoint(id, ++lessonVisit);
  }
  if (!state.checkpointLoaded) return loadingLesson();
  if (state.phase === 'complete') return completedLesson();
  const isExercise = state.phase === 'exercises';
  const current = isExercise ? lesson.exercises[state.index] : lesson.cards[state.index];
  const total = lesson.cards.length + lesson.exercises.length;
  const position = isExercise ? lesson.cards.length + state.index : state.index;
  return shell(`<main id="main-content" class="page lesson-page" tabindex="-1"><div class="lesson-toolbar"><a class="text-link" href="#dashboard">← Πίσω στη διαδρομή</a><span>ΜΑΘΗΜΑ ${pos+1} · ${escape(lesson.title)}</span></div><div class="lesson-progress" role="progressbar" aria-label="Πρόοδος μαθήματος" aria-valuenow="${position}" aria-valuemin="0" aria-valuemax="${total}"><span class="progress-step step-${position}"></span></div><p id="lesson-save-status" class="save-status" role="status" aria-live="polite">${state.resumed ? 'Συνεχίζεις από την τελευταία αποθηκευμένη θέση σου.' : state.saved ? 'Η θέση σου αποθηκεύτηκε στον λογαριασμό σου.' : 'Η θέση σου αποθηκεύεται καθώς προχωράς.'}</p><div class="lesson-title"><div><span class="eyebrow">${isExercise ? 'Η ΣΕΙΡΑ ΣΟΥ ΝΑ ΔΟΚΙΜΑΣΕΙΣ' : 'ΑΣ ΑΝΑΚΑΛΥΨΟΥΜΕ ΚΑΤΙ ΝΕΟ'}</span><h1>${isExercise ? 'Μικρή εξάσκηση, μεγάλη διαφορά.' : 'Βλέπω. Ακούω. Μαθαίνω.'}</h1></div><span class="count-pill">${state.index+1} / ${isExercise ? lesson.exercises.length : lesson.cards.length}</span></div><div class="classroom-grid"><section class="whiteboard" aria-label="Διαδραστικός εκπαιδευτικός πίνακας"><div class="board-topline"><span class="tiny-dot"></span> Ο ΠΙΝΑΚΑΣ ΣΟΥ <span>${isExercise ? 'ΕΞΑΣΚΗΣΗ' : 'ΑΝΑΚΑΛΥΨΗ'}</span></div>${isExercise ? exerciseContent(current) : `<div class="word-presentation">${current.image ? `<img class="word-image" src="${escape(current.image)}" alt="${escape(current.translation)}">` : `<div class="letter-illustration" aria-hidden="true">${state.index === 0 ? 'Aa' : state.index === 1 ? 'Bb' : 'Cc'}<span>✳</span></div>`}<h2 lang="en">${escape(current.word)}</h2><p class="word-translation">${escape(current.translation)}</p><button class="button button-outline button-small" data-action="speak-card">${icon('voice')} Άκου τη φράση</button><p class="pronunciation">Περίπου: ${escape(current.pronunciation)} <span>· Ενδεικτική βοήθεια, όχι φωνητική μεταγραφή</span></p><div class="example-box"><strong lang="en">${escape(current.example)}</strong><span>${escape(current.exampleTranslation)}</span></div></div>`}</section><aside class="teacher-panel"><div class="teacher-heading"><span class="teacher-mark">${icon('book')}</span><span><strong>Μαζί, βήμα βήμα</strong><small>Δοκιμαστικός καθηγητής</small></span></div><span class="demo-pill">Προκαθορισμένες εξηγήσεις</span><div class="teacher-message"><p>${isExercise ? 'Τώρα δοκίμασε μόνος σου. Γράψε την απάντησή σου στα αγγλικά. Αν δυσκολευτείς, υπάρχει μια μικρή βοήθεια.' : state.index === 0 ? `Καλώς ήρθες! ${escape(lesson.objective)} Παρατήρησε τον πίνακα και άκου τη λέξη ή τη φράση όταν είσαι έτοιμος.` : 'Δες το νέο παράδειγμα στον πίνακα. Μπορείς να το ακούσεις όσες φορές θέλεις, πριν συνεχίσεις.'}</p></div>${isExercise ? `<button class="button button-outline button-full" data-action="hint">Χρειάζομαι μια βοήθεια ${icon('help')}</button><p id="hint-text" class="hint-text" hidden>${escape(current.hint)}</p>` : `<div class="teacher-tip">${icon('sun')}<p>Πες τη φράση δυνατά, ακόμη κι αν δεν ακούγεται τέλεια στην αρχή.</p></div>`}<p class="small-note">Δεν υπάρχει ακόμη ελεύθερος διάλογος με AI ή αξιολόγηση προφοράς. Οι εξηγήσεις αυτού του μαθήματος είναι γραμμένες εκ των προτέρων.</p></aside></div>${!isExercise ? `<div class="lesson-actions"><span class="quiet">Χωρίς βιασύνη. Ο ρυθμός είναι δικός σου.</span><button class="button button-primary" data-action="next-card">${state.index === lesson.cards.length-1 ? 'Πάμε για εξάσκηση' : 'Επόμενο παράδειγμα'} ${icon('arrow')}</button></div>` : ''}</main>`, {lesson:true});
}
function exerciseContent(exercise) {
  return `<div class="exercise-content"><span class="exercise-type">${exercise.type === 'gap' ? 'ΣΥΜΠΛΗΡΩΣΗ ΚΕΝΟΥ' : 'ΓΡΑΠΤΗ Ή ΠΡΟΦΟΡΙΚΗ ΑΠΑΝΤΗΣΗ'}</span><h2>${escape(exercise.prompt)}</h2><form id="exercise-form"><label class="answer-label" for="answer">Η απάντησή σου στα αγγλικά</label><div class="answer-input-row"><input id="answer" name="answer" lang="en" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="160" required ${state.correct ? 'readonly' : ''} value="${state.correct ? escape(state.answers[state.index]) : ''}" placeholder="Γράψε εδώ…">${!state.correct && ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window) ? `<button type="button" class="round-button mic-button" data-action="mic" aria-label="Απάντησε με το μικρόφωνο">${icon('mic')}</button>` : ''}</div>${!state.correct ? `<label class="checkbox-label voice-consent"><input type="checkbox" id="mic-consent" ${state.micConsent ? 'checked' : ''}><span>Προαιρετικά: επιτρέπω φωνητική αναγνώριση. Ο browser ενδέχεται να στείλει ήχο στον δικό του πάροχο. Η εφαρμογή δεν αποθηκεύει ήχο. <a href="#help">Περισσότερα</a></span></label><p class="small-note">Η φωνή μετατρέπεται σε κείμενο· δεν αξιολογείται η προφορά. Μπορείς πάντα να πληκτρολογήσεις.</p>` : ''}<div id="answer-feedback" class="answer-feedback ${state.feedback?.kind || ''}" role="status" aria-live="polite" ${state.feedback ? '' : 'hidden'}>${state.feedback ? `<strong>${escape(state.feedback.title)}</strong><span>${escape(state.feedback.text)}</span>` : ''}</div><button class="button ${state.correct ? 'button-primary' : 'button-primary'} button-full" type="submit">${state.correct ? state.index === state.lesson.exercises.length-1 ? 'Ολοκλήρωση μαθήματος' : 'Συνέχεια' : 'Έλεγχος απάντησης'} ${icon(state.correct ? 'arrow' : 'check')}</button></form></div>`;
}
function completedLesson() {
  const lesson = state.lesson;
  return shell(`<main id="main-content" class="page completion-page" tabindex="-1"><div class="completion-art" aria-hidden="true">${icon('check')}<span>✳</span><i>✦</i></div><span class="eyebrow">ΚΑΘΕ ΜΙΚΡΟ ΒΗΜΑ ΜΕΤΡΑΕΙ</span><h1>Μπράβο, το ολοκλήρωσες!</h1><p>Το μάθημα «${escape(lesson.title)}» αποθηκεύτηκε<br>στον λογαριασμό σου.</p><div class="completion-stats"><span>${icon('check')}<strong>${lesson.exercises.length} απαντήσεις</strong><small>Δοκιμάστηκαν σωστά</small></span><span>${icon('star')}<strong>${state.session.progress.xp} XP</strong><small>Συνολικοί πόντοι</small></span></div><p class="small-note">Οι πόντοι προστίθενται μία φορά ανά μάθημα.<br>Οι επαναλήψεις είναι πάντα διαθέσιμες.</p><a class="button button-primary" href="#dashboard">Πίσω στη διαδρομή μου ${icon('arrow')}</a></main>`);
}
function progressView() {
  const completed = state.session.progress.completedLessons;
  const words = state.curriculum.lessons.filter(lesson => completed.includes(lesson.id)).flatMap(lesson => lesson.cards);
  return shell(`<main id="main-content" class="page" tabindex="-1"><div class="page-intro"><span class="eyebrow">ΟΛΑ ΤΑ ΜΙΚΡΑ ΣΟΥ ΒΗΜΑΤΑ</span><h1>Η πρόοδός σου, με μια ματιά.</h1><p>Όσα ολοκλήρωσες σε αυτή τη δοκιμαστική διαδρομή. <a href="#review">Κάνε μια μικρή επανάληψη.</a></p></div><div class="stats-grid"><section class="stat-card">${icon('book')}<strong>${completed.length} / 3</strong><span>Ολοκληρωμένα μαθήματα</span></section><section class="stat-card">${icon('star')}<strong>${state.session.progress.xp} XP</strong><span>Πόντοι εμπειρίας</span></section><section class="stat-card">${icon('flame')}<strong>${state.session.progress.streak}</strong><span>Συνεχόμενες ημέρες</span></section></div><section class="side-card words-section"><div class="section-heading"><div><span class="eyebrow">ΜΙΑ ΜΙΚΡΗ ΕΠΑΝΑΛΗΨΗ</span><h2>Οι λέξεις και οι φράσεις σου.</h2></div></div>${words.length ? `<div class="vocabulary-grid">${words.map(word => `<div class="vocabulary-item"><strong lang="en">${escape(word.word)}</strong><span>${escape(word.translation)}</span><button class="round-button" data-speak="${escape(word.word)}" aria-label="Άκου: ${escape(word.word)}">${icon('voice')}</button></div>`).join('')}</div>` : '<div class="empty-state"><p>Η συλλογή σου ξεκινά με το πρώτο μάθημα.</p><a class="button button-primary" href="#dashboard">Κάνε την αρχή</a></div>'}<p class="small-note">Η λίστα δείχνει το υλικό των ολοκληρωμένων μαθημάτων. Δεν αποτελεί μέτρηση απομνημόνευσης ή πιστοποίηση γλωσσομάθειας. Η σειρά ημερών βασίζεται στην ώρα Αθήνας.</p></section></main>`);
}
function settings() {
  const profile = state.session.profile;
  return shell(`<main id="main-content" class="page settings-page" tabindex="-1"><div class="page-intro"><span class="eyebrow">Ο ΧΩΡΟΣ ΣΟΥ, ΟΙ ΕΠΙΛΟΓΕΣ ΣΟΥ</span><h1>Όπως σε βολεύει.</h1><p>Ρύθμισε τον στόχο σου και διαχειρίσου τα δεδομένα σου.</p></div><div class="settings-grid"><section class="side-card"><h2>Η καθημερινή σου αρχή</h2><form id="settings-form"><label>Όνομα<input value="${escape(state.session.user.name)}" readonly></label><label>Email<input type="email" value="${escape(state.session.user.email)}" readonly></label><label>Ημερήσιος στόχος<select name="goalMinutes">${[5,10,15].map(goal => `<option value="${goal}" ${profile.goalMinutes === goal ? 'selected' : ''}>${goal} λεπτά</option>`).join('')}</select></label><p id="form-error" class="form-error" role="alert" hidden></p><button class="button button-primary" type="submit">Αποθήκευση στόχου ${icon('check')}</button></form><p class="small-note">Ο στόχος είναι προσωπική προτίμηση. Δεν μετράμε ακόμη ενεργά λεπτά μελέτης. Αλλαγή email και κωδικού δεν διατίθεται στη δοκιμαστική έκδοση.</p><button class="button button-outline settings-logout" data-action="logout">${icon('logout')} Αποσύνδεση</button></section><div><section class="side-card"><h2>Η εμφάνισή σου</h2><label>Θέμα εφαρμογής<select id="theme-select"><option value="light">Φωτεινό</option><option value="dark" ${document.documentElement.dataset.theme === 'dark' ? 'selected' : ''}>Σκοτεινό</option></select></label><p class="small-note">Το θέμα αποθηκεύεται μόνο σε αυτόν τον browser. Οι ρυθμίσεις μειωμένης κίνησης της συσκευής σου εφαρμόζονται αυτόματα.</p>${installPrompt ? '<button class="button button-outline" data-action="install">Εγκατάσταση εφαρμογής</button>' : '<p class="small-note">Για εγκατάσταση, δες την επιλογή «Εγκατάσταση εφαρμογής» ή «Προσθήκη στην αρχική οθόνη» στο μενού του browser, εφόσον υποστηρίζεται.</p>'}</section><section class="side-card data-card"><h2>Τα δεδομένα σου</h2><p>Ο λογαριασμός και η πρόοδός σου αποθηκεύονται στη βάση δεδομένων αυτής της εγκατάστασης.</p><button class="button button-outline" data-action="export">${icon('download')} Εξαγωγή δεδομένων</button><button class="text-button danger-link" data-action="show-delete">Διαγραφή λογαριασμού</button><form id="delete-form" class="delete-form" hidden><p>Η διαγραφή αφαιρεί μόνιμα τον λογαριασμό και την πρόοδό σου από την ενεργή βάση. Δεν αναιρείται. Τυχόν αντίγραφα ασφαλείας χρειάζονται χωριστή πολιτική διατήρησης πριν από δημόσια χρήση.</p><label>Επιβεβαίωσε τον κωδικό σου<input type="password" name="password" autocomplete="current-password" required></label><p id="delete-error" class="form-error" role="alert" hidden></p><button class="button button-danger" type="submit">Οριστική διαγραφή</button><button class="text-button" type="button" data-action="cancel-delete">Ακύρωση</button></form></section></div></div></main>`);
}
function help() {
  return shell(`<main id="main-content" class="page help-page ${!state.session?.user ? 'wrap' : ''}" tabindex="-1"><div class="page-intro"><span class="eyebrow">ΜΕ ΚΑΘΑΡΑ ΛΟΓΙΑ</span><h1>Μερικές χρήσιμες απαντήσεις.</h1><p>Τι μπορείς να δοκιμάσεις σήμερα και τι ετοιμάζουμε για αργότερα.</p></div><div class="help-grid"><section class="side-card"><h2>Η δοκιμαστική εφαρμογή</h2><details open><summary>Τι περιλαμβάνει;</summary><p>Εγγραφή ενηλίκων, επιλογή Ελληνικά → Αγγλικά, 3 μικρά μαθήματα A1, συνέχιση από την τελευταία αποθηκευμένη θέση, προγραμματισμένες επαναλήψεις, γραπτές ασκήσεις, προαιρετική φωνητική εισαγωγή και αποθήκευση προόδου στον λογαριασμό. Μπορείς να συνδεθείς από άλλον browser στην ίδια εγκατάσταση.</p></details><details><summary>Ο καθηγητής είναι πραγματικό AI;</summary><p>Σε αυτή την έκδοση οι εξηγήσεις και οι διορθώσεις είναι προκαθορισμένες. Δεν υπάρχει ελεύθερος διάλογος, ζωντανό μοντέλο AI ή αξιολόγηση προφοράς. Η σύνδεση πραγματικού AI είναι επόμενο στάδιο.</p></details><details><summary>Πώς λειτουργεί η φωνή;</summary><p>Το κουμπί ακρόασης χρησιμοποιεί τις διαθέσιμες φωνές της συσκευής ή του browser. Η αναγνώριση ομιλίας είναι προαιρετική και ζητά ξεχωριστή συγκατάθεση και άδεια μικροφώνου. Ο browser μπορεί να στείλει τον ήχο σε δικό του πάροχο. Δεν αποθηκεύουμε ήχο· η αναγνωρισμένη απάντηση γίνεται κείμενο. Δεν λειτουργεί σε όλους τους browsers και χρειάζεται ασφαλή σύνδεση ή localhost. Η πληκτρολόγηση παραμένει διαθέσιμη.</p></details><details><summary>Υπάρχει συνδρομή ή χρέωση;</summary><p>Δεν υπάρχουν ενεργές πληρωμές, δωρεάν δοκιμή με αυτόματη χρέωση ή συνδρομές σε αυτή την εγκατάσταση. Η δοκιμαστική εφαρμογή δεν ζητά κάρτα.</p></details><details><summary>Τι γίνεται χωρίς σύνδεση;</summary><p>Ορισμένα δημόσια αρχεία της εφαρμογής αποθηκεύονται προσωρινά στον browser. Η σύνδεση, τα μαθήματα και η αποθήκευση προόδου χρειάζονται διαθέσιμο διακομιστή. Αν χαθεί η σύνδεση στην ολοκλήρωση, κράτησε ανοιχτή τη σελίδα και ξαναδοκίμασε.</p></details></section><section class="side-card"><h2>Απόρρητο & λογαριασμός</h2><details open><summary>Ποια δεδομένα κρατά η εφαρμογή;</summary><p>Όνομα, email, προστατευμένη σύνοψη κωδικού, εκπαιδευτικές προτιμήσεις, ολοκληρωμένα μαθήματα, τελευταία αποθηκευμένη θέση και σωστές απαντήσεις εντός μαθήματος, πρόγραμμα επαναλήψεων και αποτελέσματα προσπαθειών επανάληψης, πόντοι και δραστηριότητα για τη σειρά ημερών. Ένα απαραίτητο cookie κρατά τη σύνδεσή σου. Το θέμα αποθηκεύεται τοπικά. Δεν υπάρχουν analytics ή διαφημιστικά cookies.</p></details><details><summary>Πώς εξάγω ή διαγράφω τα δεδομένα μου;</summary><p>Από τις Ρυθμίσεις μπορείς να κατεβάσεις αντίγραφο δεδομένων σε JSON και να διαγράψεις τον λογαριασμό σου με επιβεβαίωση κωδικού. Η αποσύνδεση αφαιρεί τη συνεδρία του τρέχοντος browser.</p></details><details><summary>Μπορούν να τη χρησιμοποιήσουν παιδιά;</summary><p>Η παρούσα δοκιμή προορίζεται μόνο για ενηλίκους 18+. Πριν υποστηριχθούν ανήλικοι απαιτούνται κατάλληλες γονικές διαδικασίες, νομικός έλεγχος και πρόσθετη προστασία.</p></details><details><summary>Ξέχασα τον κωδικό μου.</summary><p>Η αποστολή email και η ανάκτηση κωδικού δεν έχουν ενεργοποιηθεί. Μην χρησιμοποιείς αυτή τη δοκιμή για κρίσιμα δεδομένα ή κωδικό που χρησιμοποιείς αλλού. Η δημόσια διάθεση προϋποθέτει ολοκληρωμένες διαδικασίες λογαριασμού.</p></details><details><summary>Πώς αναφέρω ένα πρόβλημα;</summary><p>Δεν έχει οργανωθεί ακόμη ανθρώπινο τμήμα υποστήριξης. Κατέγραψε τη σελίδα, τα βήματα και το μήνυμα σφάλματος και στείλε τα στον ιδιοκτήτη της δοκιμής από το κανάλι που σου έδωσε πρόσβαση. Μην στέλνεις κωδικούς ή προσωπικές ηχογραφήσεις.</p></details><div class="info-box"><p>Η παρούσα ενημέρωση περιγράφει τη δοκιμή. Δεν αντικαθιστά ελεγμένη πολιτική απορρήτου, όρους χρήσης ή νομικό έλεγχο πριν από δημόσια κυκλοφορία.</p></div></section></div></main>`);
}
function render() {
  stopVoice();
  let route = location.hash.slice(1) || 'home';
  const publicRoutes = ['home','login','register','help'];
  if (!state.session.user && !publicRoutes.includes(route)) { go('login'); return; }
  if (state.session.user && ['home','login','register'].includes(route)) { go(state.session.profile ? 'dashboard' : 'onboarding'); return; }
  if (state.session.user && !state.session.profile && !['onboarding','help'].includes(route)) { go('onboarding'); return; }
  if (!route.startsWith('lesson/') && state.lesson) { state.lesson = null; lessonVisit++; }
  if (route !== 'review' && state.review) { state.review = null; reviewVisit++; }
  let content;
  if (route === 'home') content = landing();
  else if (['login','register'].includes(route)) content = auth(route);
  else if (route === 'onboarding') content = onboarding();
  else if (route === 'dashboard') content = dashboard();
  else if (route === 'review') content = reviewView();
  else if (route === 'progress') content = progressView();
  else if (route === 'settings') content = settings();
  else if (route === 'help') content = help();
  else if (route.startsWith('lesson/')) content = lessonView(route.split('/')[1]);
  else content = shell('<main class="page" id="main-content"><h1>Αυτή η σελίδα δεν βρέθηκε.</h1><a href="#home">Πίσω στην αρχή</a></main>');
  app.innerHTML = content;
  const page = document.querySelector('#main-content');
  page?.focus({preventScroll:true});
  window.scrollTo({top:0,behavior:'instant'});
  document.title = `${route.startsWith('lesson/') ? state.lesson?.title || 'Μάθημα' : ({dashboard:'Η διαδρομή μου',progress:'Η πρόοδός μου',settings:'Ρυθμίσεις',review:'Επαναλήψεις',help:'Βοήθεια',login:'Σύνδεση',register:'Εγγραφή',onboarding:'Η δική σου αρχή'}[route] || 'Η δική σου νέα αρχή')} — AI Language Academy`;
}
function speak(text) {
  if (!('speechSynthesis' in window)) { toast('Η ακρόαση δεν υποστηρίζεται σε αυτόν τον browser. Δες το γραπτό παράδειγμα.'); return; }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'en-GB'; utterance.rate = 0.82;
  const voice = window.speechSynthesis.getVoices().find(item => item.lang === 'en-GB') || window.speechSynthesis.getVoices().find(item => item.lang.startsWith('en'));
  if (voice) utterance.voice = voice;
  utterance.onerror = () => toast('Η φωνή δεν είναι διαθέσιμη τώρα. Μπορείς να συνεχίσεις με το γραπτό παράδειγμα.');
  window.speechSynthesis.speak(utterance);
}
function startMic(button) {
  if (recognition) { stopVoice(); button.classList.remove('listening'); return; }
  if (!document.querySelector('#mic-consent')?.checked) { toast('Για φωνητική απάντηση, διάβασε και επίλεξε πρώτα την προαιρετική συγκατάθεση.'); document.querySelector('#mic-consent')?.focus(); return; }
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) { toast('Η φωνητική εισαγωγή δεν υποστηρίζεται. Μπορείς να γράψεις την απάντησή σου.'); return; }
  if (!window.isSecureContext) { toast('Το μικρόφωνο χρειάζεται ασφαλή σύνδεση HTTPS ή localhost. Χρησιμοποίησε πληκτρολόγηση.'); return; }
  recognition = new Recognition();
  recognition.lang = 'en-US'; recognition.interimResults = false; recognition.continuous = false;
  button.classList.add('listening'); button.setAttribute('aria-label','Διακοπή ακρόασης');
  toast('Σε ακούω στα αγγλικά. Μίλησε όταν εμφανιστεί η άδεια του browser.');
  recognition.onresult = event => { const input = document.querySelector('#answer'); if (input) { input.value = event.results[0][0].transcript; input.focus(); toast('Η απάντηση έγινε κείμενο. Έλεγξέ τη και πάτησε «Έλεγχος απάντησης».'); } };
  recognition.onerror = () => toast('Δεν μπόρεσα να χρησιμοποιήσω το μικρόφωνο. Έλεγξε την άδεια ή γράψε την απάντησή σου.');
  recognition.onend = () => { recognition = null; button.classList.remove('listening'); button.setAttribute('aria-label','Απάντησε με το μικρόφωνο'); };
  try { recognition.start(); } catch { recognition = null; button.classList.remove('listening'); toast('Η φωνητική εισαγωγή δεν ξεκίνησε. Μπορείς να πληκτρολογήσεις.'); }
}
function normalize(answer) { return String(answer).normalize('NFKC').toLowerCase().trim().replace(/[.!?,;:]+$/g,'').trim().replace(/\s+/g,' '); }
function formError(error, id = 'form-error') { const target = document.getElementById(id); if (target) { target.textContent = error.message; target.hidden = false; target.scrollIntoView({block:'nearest'}); } else toast(error.message); }
async function withBusy(form, callback) {
  if (busy) return;
  busy = true;
  const button = form.querySelector('button[type="submit"],button[data-action="review-retry"]');
  if (button) { button.disabled = true; button.setAttribute('aria-busy','true'); }
  try { await callback(); } catch(error) { formError(error, form.id === 'delete-form' ? 'delete-error' : 'form-error'); }
  finally { busy = false; if (button?.isConnected) { button.disabled = false; button.removeAttribute('aria-busy'); } }
}
app.addEventListener('submit', event => {
  event.preventDefault();
  const form = event.target;
  const data = Object.fromEntries(new FormData(form));
  withBusy(form, async () => {
    if (form.id === 'auth-form') {
      await api(form.dataset.mode, {method:'POST',body:{...data,ageConfirmed:data.ageConfirmed === 'on'}});
      await refresh(); go(state.session.profile ? 'dashboard' : 'onboarding');
    } else if (form.id === 'onboarding-form') {
      await api('profile',{method:'PUT',body:{...data,goalMinutes:Number(data.goalMinutes),ageGroup:'adult',level:'A1'}});
      await refresh(); go('dashboard');
    } else if (form.id === 'settings-form') {
      await api('profile',{method:'PUT',body:{...state.session.profile,goalMinutes:Number(data.goalMinutes)}});
      await refresh(); render(); toast('Ο ημερήσιος στόχος σου αποθηκεύτηκε.');
    } else if (form.id === 'delete-form') {
      await api('account',{method:'DELETE',body:{password:data.password}}); await refresh(); pendingReviewAttempt = null; go('home'); toast('Ο λογαριασμός και η πρόοδός σου διαγράφηκαν από την ενεργή βάση.');
    } else if (form.id === 'review-form') {
      await submitReviewAttempt();
    } else if (form.id === 'exercise-form') {
      if (state.correct) {
        if (state.index < state.lesson.exercises.length-1) {
          const saved = await saveLessonTransition({index:state.index+1,correct:false});
          if (saved) document.querySelector('#answer')?.focus();
        } else {
          const id = state.lesson.id, visit = lessonVisit, userId = state.session.user.id, answers = [...state.answers];
          try {
            const session = await api('progress',{method:'POST',body:{lessonId:id,answers}});
            dashboardReview = null;
            if (state.session.user?.id !== userId) return;
            const stillInLesson = learningContext(id,visit,userId);
            state.session = session;
            if (stillInLesson) state.phase='complete';
            render();
          } catch(error) {
            if (learningContext(id,visit,userId)) toast('Η πρόοδος δεν επιβεβαιώθηκε. Κράτησε τη σελίδα ανοιχτή και πάτησε ξανά ολοκλήρωση όταν επανέλθει η σύνδεση.');
          }
        }
      } else {
        const exercise = state.lesson.exercises[state.index];
        const answer = String(data.answer || '').trim();
        const correct = (exercise.acceptedAnswers || [exercise.answer]).some(value => normalize(value) === normalize(answer));
        if (correct) {
          const answers = [...state.answers]; answers[state.index] = answer;
          await saveLessonTransition({answers,correct:true});
        } else {
          state.feedback = {kind:'error',title:'Ας το ξαναδούμε μαζί.',text:`${exercise.explanation} Δοκίμασε ξανά πριν συνεχίσουμε.`};
          render(); document.querySelector('#answer')?.focus();
        }
      }
    }
  });
});
app.addEventListener('click', async event => {
  const speakButton = event.target.closest('[data-speak]');
  if (speakButton) { speak(speakButton.dataset.speak); return; }
  const button = event.target.closest('[data-action]');
  if (!button || button.disabled) return;
  const action = button.dataset.action;
  if (action === 'speak-demo') speak('car. This is a car.');
  else if (action === 'speak-card') speak(state.lesson.cards[state.index].word);
  else if (action === 'mic') startMic(button);
  else if (action === 'hint') { document.querySelector('#hint-text').hidden = false; button.setAttribute('aria-expanded','true'); }
  else if (action === 'next-card') {
    if (busy) return;
    busy = true; button.disabled = true; button.setAttribute('aria-busy','true');
    try { await saveLessonTransition(state.index < state.lesson.cards.length-1 ? {index:state.index+1} : {phase:'exercises',index:0,answers:[],correct:false}); }
    finally { busy = false; if (button.isConnected) { button.disabled = false; button.removeAttribute('aria-busy'); } }
  }
  else if (action === 'lesson-retry') { state.checkpointError = null; loadLessonCheckpoint(state.lesson.id,++lessonVisit); render(); }
  else if (action === 'review-reload') { beginReviewLoad(); render(); }
  else if (action === 'review-start-all') { if (!busy) setReviewMode('all'); }
  else if (action === 'review-start-due') { if (!busy) setReviewMode('due'); }
  else if (action === 'review-hint') { document.querySelector('#review-hint').hidden = false; button.setAttribute('aria-expanded','true'); }
  else if (action === 'review-retry') { await withBusy(document.querySelector('#review-form'),submitReviewAttempt); }
  else if (action === 'review-next') {
    if (busy || state.review.pending) return;
    Object.assign(state.review,{index:state.review.index+1,result:null,error:null,answer:'',completed:state.review.completed+1});
    render(); document.querySelector('#review-answer')?.focus();
  }
  else if (action === 'show-delete') { document.querySelector('#delete-form').hidden=false; document.querySelector('#delete-form input').focus(); }
  else if (action === 'cancel-delete') document.querySelector('#delete-form').hidden=true;
  else if (action === 'logout') {
    button.disabled = true;
    try { await api('logout',{method:'POST',body:{}}); await refresh(); pendingReviewAttempt = null; go('home'); } catch(error) { toast(error.message); button.disabled=false; }
  } else if (action === 'export') {
    button.disabled = true;
    try { const data = await api('export'); const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})); const link = document.createElement('a'); link.href=url; link.download='academy-my-data.json'; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); toast('Το αντίγραφο δεδομένων σου είναι έτοιμο.'); } catch(error) { toast(error.message); } finally { button.disabled=false; }
  } else if (action === 'install' && installPrompt) { await installPrompt.prompt(); installPrompt=null; }
});
app.addEventListener('change', event => {
  if (event.target.id === 'theme-select') { document.documentElement.dataset.theme=event.target.value; try { localStorage.setItem('academy-theme',event.target.value); } catch {} }
  if (event.target.id === 'mic-consent') { state.micConsent=event.target.checked; if (!state.micConsent) stopVoice(); }
});
document.querySelector('.skip-link').addEventListener('click', event => {
  event.preventDefault();
  const main = document.querySelector('#main-content');
  if (main) { main.setAttribute('tabindex','-1'); main.focus(); main.scrollIntoView({block:'start'}); }
});
window.addEventListener('hashchange',render);
window.addEventListener('pagehide',stopVoice);
window.addEventListener('offline',()=>toast('Η σύνδεση χάθηκε. Η ολοκλήρωση μαθήματος χρειάζεται διαθέσιμο διακομιστή.'));
window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); installPrompt=event; });
(async () => {
  try { const [session,curriculum] = await Promise.all([api('session'),api('curriculum')]); state.session=session; state.curriculum=curriculum; render(); }
  catch(error) { app.innerHTML=`<main id="main-content" class="loading-screen"><img src="/assets/logo.svg" width="54" height="54" alt=""><h1>Χρειαζόμαστε μια μικρή επανασύνδεση.</h1><p>${escape(error.message)}</p><p>Βεβαιώσου ότι ο διακομιστής λειτουργεί και ανανέωσε τη σελίδα.</p><a class="button button-primary" href="/">Δοκίμασε ξανά</a></main>`; }
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
})();
