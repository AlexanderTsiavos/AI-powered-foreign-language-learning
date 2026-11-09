import {renderExerciseExtras,renderExerciseInput} from './exercises.js';
import {renderAssessmentPage} from './assessment-ui.js';
import {renderSupportPage,renderNotificationsPage} from './support-ui.js';
import {renderAdminPage} from './admin-ui.js';
import {emailAvailability,renderAccountEmail,renderMailPage} from './account-mail-ui.js';
import {renderLegalPage} from './legal-ui.js';
import {renderStudyRecommendation,renderStudyProgress,renderLessonMap} from './study-plan-ui.js';
import {renderLibraryPage,renderLibraryResults,filteredLibraryCards} from './library-ui.js';
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
  heart: '<path d="M20 5c-3-3-6-1-8 1-2-2-5-4-8-1-6 6 8 15 8 15S26 11 20 5Z"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>'
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
async function api(path, {method = 'GET', body, signal} = {}) {
  const headers = {'Accept':'application/json'};
  if (body) headers['Content-Type'] = 'application/json';
  let response;
  try { response = await fetch(`/api/${path}`, {method,headers,signal,credentials:'same-origin',cache:'no-store',body:body ? JSON.stringify({...body,csrfToken:state.session?.csrfToken}) : undefined}); }
  catch(error) { if (error.name === 'AbortError') throw error; throw new Error('Δεν υπάρχει σύνδεση με τον διακομιστή. Δοκίμασε ξανά όταν επανέλθει.'); }
  let data;
  try { data = await response.json(); } catch { throw new Error('Ο διακομιστής δεν απάντησε σωστά. Δοκίμασε ξανά.'); }
  if (!response.ok) { const error=new Error(data.error || 'Κάτι δεν πήγε καλά. Δοκίμασε ξανά.'); error.status=response.status; throw error; }
  return data;
}
async function refresh() { state.session = await api('session'); dashboardReview = null; state.studyPlan=null; }
let emailStatus = null;
let mailView = null;
const mailKinds = ['reset-request','reset-password','verify-email','confirm-email-change'];
function loadEmailStatus(force=false) {
  if (emailStatus && !force) return;
  const entry={loading:true,available:false,error:false};
  emailStatus=entry;
  const update=()=>{
    if(emailStatus!==entry) return;
    const target=document.querySelector('#email-status');
    if(target) target.textContent=emailAvailability(entry);
    document.querySelectorAll('[data-email-submit]').forEach(button=>{ button.disabled=!entry.available || button.dataset.emailVerified==='true'; });
  };
  update();
  api('account/email/status').then(data=>{
    if(emailStatus!==entry) return;
    Object.assign(entry,{loading:false,available:data.available===true}); update();
  }).catch(()=>{
    if(emailStatus!==entry) return;
    Object.assign(entry,{loading:false,available:false,error:true}); update();
  });
}
function currentMailView(route) {
  if(mailView?.routeKey===route) return mailView;
  const [kind,token,...extra]=route.split('/');
  mailView={routeKey:route,kind,token:!extra.length && /^[A-Za-z0-9_-]{20,512}$/.test(token || '') ? token : '',done:false};
  return mailView;
}
function mailMatches(view,epoch) { return mailView===view && authEpoch===epoch && location.hash.slice(1)===view.routeKey; }
function finishMailView(view,success,message) {
  if(mailView!==view || location.hash.slice(1)!==view.routeKey) { view.token=''; render(); return; }
  view.token=''; view.done=true; view.success=success; view.message=message;
  view.routeKey=view.kind;
  // Replace the fragment, so a consumed secret does not remain in browser history.
  history.replaceState(history.state,'',`${location.pathname}${location.search}#${view.kind}`);
  render();
}
async function submitMailForm(form,data) {
  const epoch=authEpoch;
  if(form.id==='reset-request-form') {
    if(!emailStatus?.available) throw new Error(emailAvailability(emailStatus));
    const view=mailView;
    const result=await api('account/password/reset/request',{method:'POST',body:{email:data.email}});
    if(!mailMatches(view,epoch) || !form.isConnected) return;
    const target=form.querySelector('#reset-request-result');
    target.textContent=result.message || 'Αν το email αντιστοιχεί σε λογαριασμό, θα σταλεί σύνδεσμος ανάκτησης. Έλεγξε και τα ανεπιθύμητα.';
    target.hidden=false;
    form.querySelector('#mail-action-error').hidden=true;
    return;
  }
  if(form.id==='verify-email-request-form' || form.id==='change-email-form') {
    if(!emailStatus?.available) throw new Error(emailAvailability(emailStatus));
    const userId=state.session.user?.id;
    if(!userId) throw new Error('Συνδέσου ξανά για να συνεχίσεις.');
    const change=form.id==='change-email-form';
    const result=await api(change ? 'account/email/change' : 'account/email/verification',{method:'POST',body:change ? {currentPassword:data.currentPassword,newEmail:data.newEmail} : {}});
    if(!accountMatches(userId,epoch) || !form.isConnected || location.hash!=='#settings') return;
    const target=form.querySelector(change ? '#change-email-result' : '#email-request-result');
    target.textContent=result.message || (change ? 'Στάλθηκε σύνδεσμος στη νέα διεύθυνση. Το email σου δεν έχει αλλάξει ακόμη.' : 'Στάλθηκε σύνδεσμος επιβεβαίωσης. Έλεγξε και τα ανεπιθύμητα.');
    target.hidden=false;
    form.querySelector('.form-error').hidden=true;
    if(change) form.reset();
    return;
  }
  const view=mailView;
  if(!view?.token || !mailMatches(view,epoch)) throw new Error('Άνοιξε ξανά ολόκληρο τον σύνδεσμο από το email σου.');
  if(view.kind==='reset-password' && data.newPassword!==data.confirmPassword) throw new Error('Οι δύο νέοι κωδικοί δεν ταιριάζουν.');
  const endpoint={'reset-password':'account/password/reset/confirm','verify-email':'account/email/verify','confirm-email-change':'account/email/change/confirm'}[view.kind];
  let result;
  try { result=await api(endpoint,{method:'POST',body:{token:view.token,...(view.kind==='reset-password' ? {newPassword:data.newPassword} : {})}}); }
  catch(error) {
    if(mailMatches(view,epoch) && [404,410].includes(error.status)) { finishMailView(view,false,error.message); return; }
    throw error;
  }
  if(authEpoch!==epoch) { view.token=''; return; }
  if(result.sessionRevoked) {
    // This response carries a fresh anonymous CSRF session, never a login.
    authEpoch++; resetPrivateViews();
    state.session={...result,user:null};
    finishMailView(view,true,result.message || 'Η ενέργεια ολοκληρώθηκε.');
  } else {
    // The token is already consumed. A failed follow-up refresh must never turn
    // a successful verification into a failure or keep the secret in the URL.
    finishMailView(view,true,result.message || 'Η ενέργεια ολοκληρώθηκε.');
    try {
      const session=await api('session');
      if(authEpoch!==epoch) return;
      state.session=session;
      if(mailView===view) render();
    } catch {
      if(mailView===view && authEpoch===epoch) toast('Το email επιβεβαιώθηκε. Ανανέωσε τη σελίδα για να ενημερωθούν οι ρυθμίσεις σου.');
    }
  }
}
function go(route) { if (location.hash === `#${route}`) render(); else location.hash = route; }
function stopVoice() {
  if (recognition) { const active = recognition; recognition = null; active.onend = null; active.onresult = null; active.onerror = null; active.abort(); }
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  document.querySelectorAll('.listening').forEach(button=>{ button.classList.remove('listening'); if (button.dataset.micIdleLabel) button.setAttribute('aria-label',button.dataset.micIdleLabel); });
}
function navItem(route, label, glyph) { return `<a href="#${route}" class="nav-link ${location.hash === `#${route}` ? 'active' : ''}">${icon(glyph)}<span>${label}</span></a>`; }
function shell(content, {lesson = false} = {}) {
  const user = state.session?.user;
  const header = `<a class="brand" href="#${user ? 'dashboard' : 'home'}"><img src="/assets/logo.svg" alt="" width="40" height="40"><span>AI Language<span class="brand-second">Academy<span class="brand-dot">.</span></span></span></a>`;
  if (!user) return `<header class="public-header wrap">${header}<nav aria-label="Κύρια πλοήγηση"><a class="text-link hide-small" href="#help">Πώς λειτουργεί</a><a class="button button-small button-outline" href="#login">Σύνδεση ${icon('arrow')}</a></nav></header>${content}<footer class="public-footer wrap"><span>Μια νέα γλώσσα. Ένας νέος κόσμος.</span><nav class="legal-links" aria-label="Πληροφορίες εφαρμογής"><a href="#help">Βοήθεια</a><a href="#privacy">Απόρρητο</a><a href="#terms">Όροι χρήσης</a></nav><span>Δοκιμαστική έκδοση · 18+</span></footer>`;
  return `<div class="workspace"><aside class="sidebar">${header}<span class="sidebar-label">Ο ΔΙΚΟΣ ΣΟΥ ΧΩΡΟΣ</span><nav aria-label="Κύρια πλοήγηση">${navItem('dashboard','Η διαδρομή μου','home')}${navItem('review','Επαναλήψεις','repeat')}${navItem('teacher','Καθηγητής AI','voice')}${navItem('progress','Η πρόοδός μου','chart')}${navItem('library','Το λεξικό μου','book')}${navItem('settings','Ρυθμίσεις','settings')}${navItem('help','Βοήθεια','help')}${user.isAdmin === true ? navItem('admin','Διαχείριση','lock') : ''}</nav><div class="sidebar-note">${icon('heart')}<p>Κάθε μικρό βήμα<br>σε πάει πιο μακριά.</p></div><button class="user-block" data-action="logout" aria-label="Αποσύνδεση"><span class="avatar">${escape(user.name.slice(0,1).toUpperCase())}</span><span>${escape(user.name)}<small>Δοκιμαστικός λογαριασμός</small></span>${icon('logout')}</button></aside><div class="workspace-main"><header class="app-header"><a class="mobile-brand" href="#dashboard"><img src="/assets/logo.svg" alt="Academy" width="34" height="34"></a><span class="course-pill"><span aria-hidden="true">🇬🇧</span> Αγγλικά <span class="level-label">A1</span></span><div class="header-metrics"><a id="notification-bell" class="round-button notification-bell" href="#notifications" aria-label="Ειδοποιήσεις">${icon('bell')}<span id="notification-badge" class="notification-badge" ${notificationSummary?.unreadCount ? '' : 'hidden'}>${Math.min(notificationSummary?.unreadCount || 0,99)}</span></a><span title="Πόντοι εμπειρίας">${icon('star')} ${state.session.progress?.xp || 0} <span class="hide-small">XP</span></span><span title="Συνεχόμενες ημέρες μαθημάτων">${icon('flame')} ${state.session.progress?.streak || 0}</span><button class="mobile-logout round-button" data-action="logout" aria-label="Αποσύνδεση">${icon('logout')}</button></div></header>${content}</div><nav class="mobile-nav" aria-label="Πλοήγηση κινητού">${navItem('dashboard','Διαδρομή','home')}${navItem('review','Επανάληψη','repeat')}${navItem('progress','Πρόοδος','chart')}${navItem('settings','Ρυθμίσεις','settings')}${navItem('teacher','Καθηγητής','voice')}</nav></div>`;
}
function landing() {
  return shell(`<main id="main-content" class="landing wrap" tabindex="-1"><section class="hero"><div class="hero-copy"><span class="eyebrow"><span class="tiny-dot"></span> Η ΝΕΑ ΣΟΥ ΑΡΧΗ ΣΤΑ ΑΓΓΛΙΚΑ</span><h1>Μια νέα γλώσσα.<br>Ένας κόσμος<br><em>δυνατοτήτων.</em></h1><p class="hero-description">Μάθε αγγλικά με μικρά, ξεκάθαρα μαθήματα και έναν διαδραστικό πίνακα. Στον δικό σου ρυθμό, από την πρώτη σου λέξη.</p><a class="button button-primary button-large" href="#register">Ας κάνουμε την αρχή ${icon('arrow')}</a><p class="quiet hero-footnote">Χωρίς κάρτα · Δωρεάν δοκιμαστική εφαρμογή · Για ενήλικες</p><div class="hero-trust"><span class="mini-avatars"><b>α</b><b>β</b><b>γ</b></span><span><strong>Από το «δεν ξέρω» στο «μπορώ».</strong><br>Ελληνικές εξηγήσεις. Αληθινά μικρά βήματα.</span></div></div><div class="hero-visual" aria-label="Παράδειγμα εκπαιδευτικού πίνακα"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><span class="floating-label label-top">${icon('sun')} Μια λέξη τη φορά.</span><div class="demo-board"><div class="board-topline"><span class="tiny-dot"></span> Ο ΠΙΝΑΚΑΣ ΣΟΥ <span>01 / 03</span></div><img class="hero-car" src="/assets/car.svg" alt="Πρωτότυπη εικονογράφηση αυτοκινήτου"><div class="demo-word">car <button class="round-button" data-action="speak-demo" aria-label="Άκου τη λέξη car">${icon('voice')}</button></div><span class="demo-translation">αυτοκίνητο</span><div class="demo-example"><span>This is a car.</span><small>Αυτό είναι ένα αυτοκίνητο.</small></div></div><div class="floating-label label-bottom"><span class="success-bubble">${icon('check')}</span><span><strong>Η πρώτη σου λέξη!</strong><small>Κάθε αρχή μετράει.</small></span></div><span class="decor-star" aria-hidden="true">✳</span></div></section><section class="landing-benefits" aria-label="Ο τρόπος μας"><div><span class="feature-icon">${icon('clock')}</span><span><strong>5 λεπτά αρκούν για αρχή</strong><small>Μικρά μαθήματα, χωρίς βιασύνη.</small></span></div><div><span class="feature-icon peach">${icon('book')}</span><span><strong>Βλέπεις. Ακούς. Δοκιμάζεις.</strong><small>Ένας πίνακας γεμάτος ανακαλύψεις.</small></span></div><div><span class="feature-icon yellow">${icon('globe')}</span><span><strong>Από τα ελληνικά στα αγγλικά</strong><small>Εξηγήσεις που καταλαβαίνεις.</small></span></div></section><section class="honesty-note"><strong>Μια εφαρμογή που μεγαλώνει μαζί μας.</strong> Η πρώτη έκδοση περιλαμβάνει ${state.curriculum.lessons.length} μαθήματα A1 με προκαθορισμένες εξηγήσεις. Η ξεχωριστή συνομιλία AI λειτουργεί μόνο όταν ο ιδιοκτήτης συνδέσει τοπικό μοντέλο.</section></main>`);
}
function auth(mode) {
  const register = mode === 'register';
  return shell(`<main id="main-content" class="auth-page wrap" tabindex="-1"><section class="auth-aside"><span class="eyebrow">ΕΝΑ ΜΙΚΡΟ ΒΗΜΑ ΓΙΑ ΣΕΝΑ</span><h1>Η επόμενη<br>ιστορία σου<br><em>ξεκινά εδώ.</em></h1><p>Δεν χρειάζεται να ξέρεις ήδη αγγλικά.<br>Αρκεί να θέλεις να κάνεις την αρχή.</p><div class="auth-art" aria-hidden="true">Aa<span>Hello!</span><i>Γεια σου!</i></div></section><section class="form-card"><span class="eyebrow">${register ? 'ΚΑΛΩΣ ΗΡΘΕΣ' : 'ΚΑΛΩΣ ΕΠΕΣΤΡΕΨΕΣ'}</span><h2>${register ? 'Ας γνωριστούμε.' : 'Συνέχισε τη διαδρομή σου.'}</h2><p class="quiet">${register ? 'Δημιούργησε τον δοκιμαστικό σου λογαριασμό.' : 'Συνδέσου στον λογαριασμό σου.'}</p><form id="auth-form" data-mode="${mode}">${register ? '<label>Πώς σε λένε;<input name="name" autocomplete="given-name" required minlength="2" maxlength="60" placeholder="Το όνομά σου"></label>' : ''}<label>Email<input type="email" name="email" autocomplete="email" required maxlength="254" placeholder="onoma@example.com"></label><label>Κωδικός<input type="password" name="password" autocomplete="${register ? 'new-password' : 'current-password'}" required minlength="${register ? '10' : '1'}" maxlength="256" placeholder="${register ? 'Τουλάχιστον 10 χαρακτήρες' : 'Ο κωδικός σου'}"></label>${register ? '<label class="checkbox-label"><input type="checkbox" name="ageConfirmed" required><span>Είμαι 18 ετών ή μεγαλύτερος/η και διάβασα την <a href="#privacy">ενημέρωση απορρήτου</a> και το <a href="#terms">προσχέδιο όρων χρήσης</a>.</span></label>' : ''}<p id="form-error" class="form-error" role="alert" hidden></p><button class="button button-primary button-full" type="submit">${register ? 'Δημιουργία λογαριασμού' : 'Σύνδεση'} ${icon('arrow')}</button></form>${!register ? '<p class="forgot-password"><a href="#reset-request">Ξέχασα τον κωδικό μου</a></p>' : ''}<p class="auth-switch">${register ? 'Έχεις ήδη λογαριασμό; <a href="#login">Συνδέσου</a>' : 'Πρώτη φορά εδώ; <a href="#register">Κάνε την αρχή</a>'}</p><p class="small-note">Ιδιωτική δοκιμή για ενηλίκους. Η αποστολή συνδέσμων λογαριασμού λειτουργεί μόνο όταν έχει ενεργοποιηθεί σε αυτή την εγκατάσταση. Χρησιμοποίησε μοναδικό κωδικό.</p></section></main>`);
}
function onboarding() {
  return shell(`<main id="main-content" class="page onboarding-page" tabindex="-1"><div class="page-intro"><span class="eyebrow">ΠΡΙΝ ΚΑΝΟΥΜΕ ΤΗΝ ΑΡΧΗ</span><h1>Μια διαδρομή στα μέτρα σου.</h1><p>Λίγα πράγματα για σένα, για να ξεκινήσουμε σωστά.</p></div><form id="onboarding-form" class="form-card wide-form"><div class="two-columns"><label>Η γλώσσα μου<select name="nativeLanguage"><option value="el">Ελληνικά</option></select></label><label>Θέλω να μάθω<select name="targetLanguage"><option value="en">Αγγλικά</option></select></label></div><label>Γιατί θέλεις να μάθεις αγγλικά;<select name="reason"><option value="travel">Για ταξίδια και νέες εμπειρίες</option><option value="work">Για τη δουλειά μου</option><option value="study">Για σπουδές</option><option value="personal">Για εμένα και την καθημερινότητά μου</option></select></label><fieldset><legend>Πόσο χρόνο θα ήθελες κάθε μέρα;</legend><div class="goal-options"><label><input type="radio" name="goalMinutes" value="5"><span>5 λεπτά<small>Μια μικρή αρχή</small></span></label><label><input type="radio" name="goalMinutes" value="10" checked><span>10 λεπτά<small>Ένας όμορφος ρυθμός</small></span></label><label><input type="radio" name="goalMinutes" value="15"><span>15 λεπτά<small>Ένα βήμα παραπάνω</small></span></label></div></fieldset><div class="info-box">${icon('book')}<p><strong>Ξεκινάμε από την αρχή, στο A1.</strong> Η δοκιμαστική έκδοση έχει ${state.curriculum.lessons.length} εισαγωγικά μαθήματα. Μπορείς να δοκιμάσεις και έξι εισαγωγικές ερωτήσεις, χωρίς αλλαγή επιπέδου ή πιστοποίηση. Οι υπόλοιπες γλώσσες θα ακολουθήσουν.</p></div><p id="form-error" class="form-error" role="alert" hidden></p><button class="button button-primary" type="submit">Στη δική μου διαδρομή ${icon('arrow')}</button></form></main>`);
}
function dashboard() {
  const progress=state.session.progress,lessons=state.curriculum.lessons;
  const completed=progress.completedLessons || [];
  loadDashboardReview();
  const plan=studyPlanView();
  return shell(`<main id="main-content" class="page dashboard-page" tabindex="-1"><div class="dashboard-heading"><div><span class="eyebrow">ΚΑΘΕ ΜΕΡΑ, ΛΙΓΟ ΠΙΟ ΚΟΝΤΑ</span><h1>Γεια σου, ${escape(state.session.user.name.split(' ')[0])}<span class="greeting-spark" aria-hidden="true">✳</span></h1><p>Η καλύτερη στιγμή για ένα μικρό βήμα είναι τώρα.</p></div><div class="daily-badge">${icon('sun')}<span>Ο στόχος σου<strong>${state.session.profile.goalMinutes} λεπτά / ημέρα</strong></span></div></div><div class="dashboard-columns"><div class="journey-column">${renderStudyRecommendation(plan,{escape,icon})}${renderLessonMap(lessons,completed,{escape,icon})}</div><aside class="dashboard-side">${dashboardReviewCard()}<section class="side-card progress-mini"><div class="section-heading"><h3>Η αρχή σου σε αριθμούς</h3>${icon('chart')}</div><div class="progress-ring"><strong>${completed.length}<small>από ${lessons.length} μαθήματα</small></strong></div><div class="mini-stat-row"><span>Πόντοι εμπειρίας</span><strong>${progress.xp} XP</strong></div><div class="mini-stat-row"><span>Συνεχόμενες ημέρες</span><strong>${progress.streak}</strong></div><a class="text-link" href="#progress">Δες την πρόοδό σου ${icon('arrow')}</a></section><section class="side-card tip-card"><span class="tip-symbol" aria-hidden="true">“</span><h3>Δεν χρειάζεται να<br>είσαι τέλειος.</h3><p>Χρειάζεται μόνο να επιστρέφεις. Τα λάθη είναι μέρος της διαδρομής.</p><span class="eyebrow">ΜΙΑ ΜΙΚΡΗ ΥΠΕΝΘΥΜΙΣΗ</span></section><p class="side-disclaimer">Δοκιμαστική διαδρομή · ${lessons.length} εισαγωγικά μαθήματα A1<br>Η ολοκλήρωση δεν πιστοποιεί επίπεδο γλωσσομάθειας.</p></aside></div></main>`);
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
  const id = state.lesson.id, visit = lessonVisit, userId = state.session.user.id, epoch=authEpoch;
  const target = {lessonId:id,phase:state.phase,index:state.index,answers:[...state.answers],correct:state.correct,...patch};
  const status = document.querySelector('#lesson-save-status');
  if (status) status.textContent = 'Αποθηκεύεται η θέση σου…';
  const key = `${userId}:${id}`;
  const request = api('lesson-state',{method:'PUT',body:target});
  pendingLessonSaves.set(key,request);
  try {
    await request;
    invalidateStudyPlan(userId,epoch);
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
function isReviewRoute() { return ['#review','#review/mistakes'].includes(location.hash); }
function reviewMatches(visit,userId) { return reviewVisit === visit && state.review?.visit === visit && state.session.user?.id === userId && state.review.route===location.hash && isReviewRoute(); }
const reviewKey = item => JSON.stringify([item.lessonId,item.exerciseId]);
function reviewQueue(review,mode) {
  return mode==='all' ? [...review.items] : mode==='mistakes' ? review.items.filter(item=>review.needsPractice.has(reviewKey(item))) : review.items.filter(item=>item.isDue);
}
function refreshPracticeInsights(review,{refreshQueue=false}={}) {
  const request={}; review.insightsRequest=request; review.insightsLoading=true; review.insightsError=null;
  api('practice-insights').then(data=>{
    if(review.insightsRequest!==request || !reviewMatches(review.visit,review.userId)) return;
    review.insightsLoading=false; review.needsPractice=new Set(data.items.map(reviewKey));
    if(refreshQueue && review.mode==='mistakes' && !review.pending && !review.result) Object.assign(review,{queue:reviewQueue(review,'mistakes'),index:0,answer:'',completed:0});
    render();
  }).catch(error=>{
    if(review.insightsRequest!==request || !reviewMatches(review.visit,review.userId)) return;
    review.insightsLoading=false; review.insightsError=error.message; render();
  });
}
function beginReviewLoad(modeOverride=null) {
  const visit=++reviewVisit,userId=state.session.user.id,mode=modeOverride || (location.hash==='#review/mistakes' ? 'mistakes' : 'due'),insightsRequest={};
  state.review={visit,userId,route:location.hash,loading:true,error:null,items:[],queue:[],index:0,mode,result:null,pending:null,answer:'',completed:0,needsPractice:new Set(),insightsLoading:true,insightsError:null,insightsRequest};
  Promise.all([api('review'),api('practice-insights').catch(error=>({loadError:error.message}))]).then(([data,insights])=>{
    if(!reviewMatches(visit,userId)) return;
    const review=state.review;
    Object.assign(review,{loading:false,items:data.items,dueCount:data.dueCount});
    if(review.insightsRequest===insightsRequest) Object.assign(review,{insightsLoading:false,insightsError:insights.loadError || null,needsPractice:new Set((insights.items || []).map(reviewKey))});
    review.queue=reviewQueue(review,review.mode);
    if(pendingReviewAttempt?.userId===userId) {
      const index=data.items.findIndex(item=>item.lessonId===pendingReviewAttempt.lessonId && item.exerciseId===pendingReviewAttempt.exerciseId);
      if(index>=0) Object.assign(review,{mode:'all',queue:[...data.items],index,pending:pendingReviewAttempt,answer:pendingReviewAttempt.answer,error:'Η προηγούμενη προσπάθεια δεν επιβεβαιώθηκε. Επανάλαβε την αποστολή για να διαβάσουμε το ίδιο αποτέλεσμα, χωρίς διπλή καταχώριση.'});
    }
    render();
  }).catch(()=>{
    if(!reviewMatches(visit,userId)) return;
    Object.assign(state.review,{loading:false,error:'Δεν μπορέσαμε να φορτώσουμε τις επαναλήψεις σου. Έλεγξε τη σύνδεση και δοκίμασε ξανά.'}); render();
  });
}
function reviewView() {
  if (!state.review) beginReviewLoad();
  const review = state.review;
  const item = review.queue[review.index];
  const intro = `<div class="page-intro"><span class="eyebrow">ΞΑΝΑΒΡΙΣΚΟΥΜΕ ΟΣΑ ΜΑΘΑΜΕ</span><h1>Μια μικρή επανάληψη.</h1><p>Λέξεις και φράσεις από τα ολοκληρωμένα μαθήματά σου, στον δικό σου ρυθμό.</p></div>`;
  if (review.loading) return shell(`<main id="main-content" class="page review-page" tabindex="-1">${intro}<div class="side-card learning-loader"><p role="status">Φορτώνουμε τις επαναλήψεις σου…</p></div></main>`);
  if (review.error && !review.items.length) return shell(`<main id="main-content" class="page review-page" tabindex="-1">${intro}<section class="side-card"><p id="review-error" class="form-error" role="alert">${escape(review.error)}</p><button class="button button-primary" data-action="review-reload">Νέα προσπάθεια φόρτωσης</button></section></main>`);
  const filters = `<div class="review-filters" aria-label="Επιλογή υλικού"><button class="button button-small ${review.mode === 'due' ? 'button-primary' : 'button-outline'}" data-action="review-start-due" ${review.pending ? 'disabled' : ''} aria-pressed="${review.mode === 'due'}">Για τώρα <span class="review-count">${review.dueCount}</span></button><button class="button button-small ${review.mode === 'all' ? 'button-primary' : 'button-outline'}" data-action="review-start-all" ${review.pending ? 'disabled' : ''} aria-pressed="${review.mode === 'all'}">Εξάσκηση όλων <span class="review-count">${review.items.length}</span></button><button class="button button-small ${review.mode === 'mistakes' ? 'button-primary' : 'button-outline'}" data-action="review-start-mistakes" ${review.pending ? 'disabled' : ''} aria-pressed="${review.mode === 'mistakes'}">Λάθη επαναλήψεων <span id="review-mistake-count" class="review-count">${review.insightsLoading ? '…' : review.insightsError ? '—' : review.needsPractice.size}</span></button></div><p class="small-note review-mistake-scope">Η προβολή λαθών δείχνει ασκήσεις με τελευταία λανθασμένη απάντηση στις επαναλήψεις. Δεν περιλαμβάνει τα λάθη της πρώτης μελέτης.</p>`;
  const note = `<div class="review-explainer"><p>Οι σωστές απαντήσεις στην ώρα τους μεταφέρουν σταδιακά την επανάληψη σε 1, 3, 7, 14 και 30 ημέρες. Αν δυσκολευτείς, το στοιχείο επιστρέφει νωρίτερα. Η προαιρετική σωστή εξάσκηση πριν από την ώρα της δεν αλλάζει την ημερομηνία. Δεν προστίθενται XP.</p><p>Το πρόγραμμα βοηθά την εξάσκηση· δεν αποτελεί μέτρηση απομνημόνευσης ή πιστοποίηση επιπέδου. Οι ώρες εμφανίζονται σε ώρα Ελλάδας.</p></div>`;
  let body;
  if(review.mode==='mistakes' && review.insightsLoading && !review.result) body='<section class="side-card learning-loader"><p role="status">Φορτώνουμε τα λάθη των επαναλήψεων…</p></section>';
  else if(review.mode==='mistakes' && review.insightsError) body=`<section class="side-card"><p class="form-error" id="review-insights-error" role="alert">${escape(review.insightsError)}</p><button class="button button-primary" type="button" data-action="review-mistakes-reload">Νέα προσπάθεια φόρτωσης λαθών</button><p class="small-note">Οι άλλες επιλογές επανάληψης παραμένουν διαθέσιμες.</p></section>`;
  else if(review.mode==='mistakes' && !item && review.items.length) body=`<section class="side-card empty-state" id="review-mistakes-empty"><span class="feature-icon">${icon(review.needsPractice.size ? 'repeat' : 'check')}</span><h2>${review.needsPractice.size ? 'Η μικρή εξάσκηση ολοκληρώθηκε.' : 'Δεν υπάρχουν καταγεγραμμένα λάθη για εξάσκηση.'}</h2><p>${review.needsPractice.size ? 'Κάποιες ασκήσεις χρειάζονται ακόμη επανάληψη. Μπορείς να δοκιμάσεις ξανά όταν είσαι έτοιμος.' : 'Στο διαθέσιμο ιστορικό, καμία μαθημένη άσκηση δεν έχει τελευταία λανθασμένη απάντηση. Συνέχισε με όλες τις ασκήσεις ή με το επόμενο μάθημα.'}</p>${review.needsPractice.size ? '<button class="button button-primary" data-action="review-start-mistakes">Ξανά στα λάθη επαναλήψεων</button>' : ''}<a class="button button-outline" href="#library">Στο προσωπικό μου λεξικό</a><a class="text-link" href="#dashboard">Στη διαδρομή μου ${icon('arrow')}</a></section>`;
  else if (!review.items.length) body = `<section class="side-card empty-state"><span class="feature-icon">${icon('book')}</span><h2>Η συλλογή σου ξεκινά με ένα μάθημα.</h2><p>Όταν ολοκληρώσεις το πρώτο μάθημα, οι ασκήσεις του θα εμφανιστούν εδώ για επανάληψη από την επόμενη ημέρα.</p><a class="button button-primary" href="#dashboard">Στη διαδρομή μου ${icon('arrow')}</a></section>`;
  else if (!item) body = `<section class="side-card empty-state"><span class="feature-icon">${icon(review.completed ? 'check' : 'sun')}</span><h2>${review.completed ? `Έκανες εξάσκηση σε ${review.completed} στοιχεία.` : 'Δεν έχεις επαναλήψεις για τώρα.'}</h2><p>${review.completed ? 'Η επόμενη επανάληψη έχει προγραμματιστεί για κάθε στοιχείο. Μπορείς να επιστρέψεις στη διαδρομή σου.' : 'Το πρόγραμμά σου θα φέρει τις λέξεις πίσω στην κατάλληλη στιγμή. Μπορείς, αν θέλεις, να εξασκηθείς και νωρίτερα.'}</p><button class="button button-primary" data-action="review-start-all">Εξάσκηση όλων ${icon('repeat')}</button><a class="text-link" href="#dashboard">Στη διαδρομή μου ${icon('arrow')}</a></section>`;
  else body = `<div class="review-classroom"><section class="whiteboard review-board"><div class="board-topline"><span class="tiny-dot"></span> ΜΙΚΡΗ ΕΠΑΝΑΛΗΨΗ <span>${review.index+1} / ${review.queue.length}</span></div><div class="exercise-content"><span class="exercise-type">${review.result ? 'ΤΟ ΑΠΟΤΕΛΕΣΜΑ ΤΗΣ ΕΠΑΝΑΛΗΨΗΣ' : item.isDue ? 'Η ΩΡΑ ΤΗΣ ΕΠΑΝΑΛΗΨΗΣ' : 'ΠΡΟΑΙΡΕΤΙΚΗ ΕΞΑΣΚΗΣΗ ΝΩΡΙΤΕΡΑ'}</span><h2>${escape(item.prompt)}</h2>${renderExerciseExtras(item)}<form id="review-form">${renderExerciseInput(item,{answer:review.answer,readonly:!!(review.pending || review.result?.correct),inputId:'review-answer'})}<p id="review-error" class="form-error" role="alert" ${review.error ? '' : 'hidden'}>${escape(review.error || '')}</p><div id="review-feedback" class="answer-feedback ${review.result ? review.result.correct ? 'success' : 'error' : ''}" role="status" aria-live="polite" ${review.result ? '' : 'hidden'}>${review.result ? `<strong>${review.result.correct ? 'Σωστά! Έκανες μια χρήσιμη επανάληψη.' : `Ας το ξαναδούμε. Σωστή απάντηση: ${escape(review.result.expectedAnswer)}`}</strong><span>${escape(review.result.explanation)}</span><span>Επόμενη επανάληψη: <strong>${escape(athensDate(review.result.nextDueAt))}</strong> · ώρα Ελλάδας.</span>${review.result.scheduleAdvanced === false && review.result.correct ? '<span>Ήταν προαιρετική εξάσκηση πριν από την ώρα της. Το πρόγραμμα επανάληψης διατηρήθηκε.</span>' : ''}` : ''}</div>${review.pending ? '<button type="button" class="button button-primary button-full" data-action="review-retry">Νέα προσπάθεια αποστολής</button><p class="small-note">Στέλνουμε ξανά την ίδια προσπάθεια. Το αποτέλεσμα καταχωρίζεται μόνο μία φορά.</p>' : !review.result?.correct ? `<button class="button button-primary button-full" type="submit">${review.result ? 'Θέλω να ξαναδοκιμάσω' : 'Έλεγχος απάντησης'} ${icon('check')}</button>` : ''}${review.result ? `<button type="button" class="button ${review.result.correct ? 'button-primary' : 'button-outline'} button-full" data-action="review-next">${review.index === review.queue.length-1 ? 'Ολοκλήρωση επανάληψης' : 'Επόμενο στοιχείο'} ${icon('arrow')}</button>` : ''}</form></div></section><aside class="side-card review-aside"><span class="feature-icon">${icon('repeat')}</span><h3>Λίγη απόσταση βοηθά.</h3><p>Μια λέξη που ξανασυναντάς μετά από λίγο καιρό γίνεται πιο οικεία. Δοκίμασε πριν δεις τη λύση.</p><p class="review-due-label">${item.isDue ? 'Προγραμματισμένη για' : 'Επόμενη επανάληψη'}<strong>${escape(athensDate(item.dueAt))}</strong><span>ώρα Ελλάδας</span></p><button class="button button-outline button-full" data-action="review-hint">Μια μικρή βοήθεια ${icon('help')}</button><p id="review-hint" class="hint-text" hidden>${escape(item.hint)}</p></aside></div>`;
  return shell(`<main id="main-content" class="page review-page" tabindex="-1">${intro}${filters}${body}${note}</main>`);
}
function setReviewMode(mode) {
  const review = state.review;
  if (review.pending) return;
  Object.assign(review,{mode,queue:reviewQueue(review,mode),index:0,result:null,error:null,answer:'',completed:0});
  render();
}
async function submitReviewAttempt() {
  const review = state.review;
  const item = review.queue[review.index];
  if (!item) return;
  const visit = review.visit, userId = state.session.user.id, epoch=authEpoch;
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
    invalidateStudyPlan(userId,epoch);
    if(accountMatches(userId,epoch) && state.review && isReviewRoute()) {
      if(state.review!==review) { beginReviewLoad(state.review.mode); render(); }
      else refreshPracticeInsights(review);
    }
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
  if (!lesson || (pos > 0 && !lessons.slice(0,pos).every(previous=>state.session.progress.completedLessons.includes(previous.id)))) return shell(`<main class="page" id="main-content"><h1>Ένα βήμα τη φορά.</h1><p>Αυτό το μάθημα ανοίγει όταν ολοκληρώσεις το προηγούμενο.</p><a class="button button-primary" href="#dashboard">Πίσω στη διαδρομή</a></main>`);
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
  return shell(`<main id="main-content" class="page lesson-page" tabindex="-1"><div class="lesson-toolbar"><a class="text-link" href="#dashboard">← Πίσω στη διαδρομή</a><span>ΜΑΘΗΜΑ ${pos+1} · ${escape(lesson.title)}</span></div><div class="lesson-progress" role="progressbar" aria-label="Πρόοδος μαθήματος" aria-valuenow="${position}" aria-valuemin="0" aria-valuemax="${total}"><span class="progress-step step-${position}"></span></div><p id="lesson-save-status" class="save-status" role="status" aria-live="polite">${state.resumed ? 'Συνεχίζεις από την τελευταία αποθηκευμένη θέση σου.' : state.saved ? 'Η θέση σου αποθηκεύτηκε στον λογαριασμό σου.' : 'Η θέση σου αποθηκεύεται καθώς προχωράς.'}</p><div class="lesson-title"><div><span class="eyebrow">${isExercise ? 'Η ΣΕΙΡΑ ΣΟΥ ΝΑ ΔΟΚΙΜΑΣΕΙΣ' : 'ΑΣ ΑΝΑΚΑΛΥΨΟΥΜΕ ΚΑΤΙ ΝΕΟ'}</span><h1>${isExercise ? 'Μικρή εξάσκηση, μεγάλη διαφορά.' : 'Βλέπω. Ακούω. Μαθαίνω.'}</h1></div><span class="count-pill">${state.index+1} / ${isExercise ? lesson.exercises.length : lesson.cards.length}</span></div><div class="classroom-grid"><section class="whiteboard" aria-label="Διαδραστικός εκπαιδευτικός πίνακας"><div class="board-topline"><span class="tiny-dot"></span> Ο ΠΙΝΑΚΑΣ ΣΟΥ <span>${isExercise ? 'ΕΞΑΣΚΗΣΗ' : 'ΑΝΑΚΑΛΥΨΗ'}</span></div>${isExercise ? exerciseContent(current) : `<div class="word-presentation">${current.image ? `<img class="word-image" src="${escape(current.image)}" alt="${escape(current.translation)}">` : `<div class="letter-illustration" aria-hidden="true">${state.index === 0 ? 'Aa' : state.index === 1 ? 'Bb' : 'Cc'}<span>✳</span></div>`}<h2 lang="en">${escape(current.word)}</h2><p class="word-translation">${escape(current.translation)}</p><button class="button button-outline button-small" data-action="speak-card">${icon('voice')} Άκου τη φράση</button>${current.pronunciation ? `<p class="pronunciation">Περίπου: ${escape(current.pronunciation)} <span>· Ενδεικτική βοήθεια, όχι φωνητική μεταγραφή</span></p>` : ''}<div class="example-box"><strong lang="en">${escape(current.example)}</strong><span>${escape(current.exampleTranslation)}</span></div></div>`}</section><aside class="teacher-panel"><div class="teacher-heading"><span class="teacher-mark">${icon('book')}</span><span><strong>Μαζί, βήμα βήμα</strong><small>Δοκιμαστικός καθηγητής</small></span></div><span class="demo-pill">Προκαθορισμένες εξηγήσεις</span><div class="teacher-message"><p>${isExercise ? 'Τώρα δοκίμασε μόνος σου. Γράψε την απάντησή σου στα αγγλικά. Αν δυσκολευτείς, υπάρχει μια μικρή βοήθεια.' : state.index === 0 ? `Καλώς ήρθες! ${escape(lesson.objective)} Παρατήρησε τον πίνακα και άκου τη λέξη ή τη φράση όταν είσαι έτοιμος.` : 'Δες το νέο παράδειγμα στον πίνακα. Μπορείς να το ακούσεις όσες φορές θέλεις, πριν συνεχίσεις.'}</p></div>${isExercise ? `<button class="button button-outline button-full" data-action="hint">Χρειάζομαι μια βοήθεια ${icon('help')}</button><p id="hint-text" class="hint-text" hidden>${escape(current.hint)}</p>` : `<div class="teacher-tip">${icon('sun')}<p>Πες τη φράση δυνατά, ακόμη κι αν δεν ακούγεται τέλεια στην αρχή.</p></div>`}<p class="small-note">Δεν υπάρχει ακόμη ελεύθερος διάλογος με AI ή αξιολόγηση προφοράς. Οι εξηγήσεις αυτού του μαθήματος είναι γραμμένες εκ των προτέρων.</p></aside></div>${!isExercise ? `<div class="lesson-actions"><span class="quiet">Χωρίς βιασύνη. Ο ρυθμός είναι δικός σου.</span><button class="button button-primary" data-action="next-card">${state.index === lesson.cards.length-1 ? 'Πάμε για εξάσκηση' : 'Επόμενο παράδειγμα'} ${icon('arrow')}</button></div>` : ''}</main>`, {lesson:true});
}
function exerciseContent(exercise) {
  const typeLabel={'gap':'ΣΥΜΠΛΗΡΩΣΗ ΚΕΝΟΥ','multiple-choice':'ΕΠΙΛΟΓΗ ΑΠΑΝΤΗΣΗΣ','word-order':'ΒΑΛΕ ΤΙΣ ΛΕΞΕΙΣ ΣΤΗ ΣΕΙΡΑ','reading':'ΚΑΤΑΝΟΗΣΗ ΚΕΙΜΕΝΟΥ','listening':'ΑΚΡΟΑΣΗ Ή ΕΝΑΛΛΑΚΤΙΚΑ ΑΝΑΓΝΩΣΗ'}[exercise.type] || 'ΓΡΑΠΤΗ Ή ΠΡΟΦΟΡΙΚΗ ΑΠΑΝΤΗΣΗ';
  return `<div class="exercise-content"><span class="exercise-type">${typeLabel}</span><h2>${escape(exercise.prompt)}</h2>${renderExerciseExtras(exercise)}<form id="exercise-form">${renderExerciseInput(exercise,{answer:state.correct ? state.answers[state.index] : '',readonly:state.correct})}${!state.correct && ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window) ? `<button type="button" class="button button-outline lesson-mic-button" data-action="mic" aria-label="Απάντησε με το μικρόφωνο">${icon('mic')} Υπαγόρευση απάντησης</button>` : ''}${!state.correct ? `<label class="checkbox-label voice-consent"><input type="checkbox" id="mic-consent" ${state.micConsent ? 'checked' : ''}><span>Προαιρετικά: επιτρέπω φωνητική αναγνώριση. Ο browser ενδέχεται να στείλει ήχο στον δικό του πάροχο. Η εφαρμογή δεν αποθηκεύει ήχο. <a href="#help">Περισσότερα</a></span></label><p class="small-note">Η φωνή μετατρέπεται σε κείμενο· δεν αξιολογείται η προφορά. Μπορείς πάντα να πληκτρολογήσεις.</p>` : ''}<div id="answer-feedback" class="answer-feedback ${state.feedback?.kind || ''}" role="status" aria-live="polite" ${state.feedback ? '' : 'hidden'}>${state.feedback ? `<strong>${escape(state.feedback.title)}</strong><span>${escape(state.feedback.text)}</span>` : ''}</div><button class="button button-primary button-full" type="submit">${state.correct ? state.index === state.lesson.exercises.length-1 ? 'Ολοκλήρωση μαθήματος' : 'Συνέχεια' : 'Έλεγχος απάντησης'} ${icon(state.correct ? 'arrow' : 'check')}</button></form></div>`;
}
function completedLesson() {
  const lesson = state.lesson;
  return shell(`<main id="main-content" class="page completion-page" tabindex="-1"><div class="completion-art" aria-hidden="true">${icon('check')}<span>✳</span><i>✦</i></div><span class="eyebrow">ΚΑΘΕ ΜΙΚΡΟ ΒΗΜΑ ΜΕΤΡΑΕΙ</span><h1>Μπράβο, το ολοκλήρωσες!</h1><p>Το μάθημα «${escape(lesson.title)}» αποθηκεύτηκε<br>στον λογαριασμό σου.</p><div class="completion-stats"><span>${icon('check')}<strong>${lesson.exercises.length} απαντήσεις</strong><small>Δοκιμάστηκαν σωστά</small></span><span>${icon('star')}<strong>${state.session.progress.xp} XP</strong><small>Συνολικοί πόντοι</small></span></div><p class="small-note">Οι πόντοι προστίθενται μία φορά ανά μάθημα.<br>Οι επαναλήψεις είναι πάντα διαθέσιμες.</p><a class="button button-primary" href="#dashboard">Πίσω στη διαδρομή μου ${icon('arrow')}</a></main>`);
}
function progressView() {
  const plan=studyPlanView();
  const completed = state.session.progress.completedLessons;
  const words = state.curriculum.lessons.filter(lesson => completed.includes(lesson.id)).flatMap(lesson => lesson.cards);
  return shell(`<main id="main-content" class="page" tabindex="-1"><div class="page-intro"><span class="eyebrow">ΟΛΑ ΤΑ ΜΙΚΡΑ ΣΟΥ ΒΗΜΑΤΑ</span><h1>Η πρόοδός σου, με μια ματιά.</h1><p>Όσα ολοκλήρωσες σε αυτή τη δοκιμαστική διαδρομή. <a href="#review">Κάνε μια μικρή επανάληψη.</a></p></div><div class="stats-grid"><section class="stat-card">${icon('book')}<strong>${completed.length} / ${state.curriculum.lessons.length}</strong><span>Ολοκληρωμένα μαθήματα</span></section><section class="stat-card">${icon('star')}<strong>${state.session.progress.xp} XP</strong><span>Πόντοι εμπειρίας</span></section><section class="stat-card">${icon('flame')}<strong>${state.session.progress.streak}</strong><span>Συνεχόμενες ημέρες</span></section></div>${renderStudyProgress(plan,{escape,icon})}<section class="side-card words-section"><div class="section-heading"><div><span class="eyebrow">ΜΙΑ ΜΙΚΡΗ ΕΠΑΝΑΛΗΨΗ</span><h2>Οι λέξεις και οι φράσεις σου.</h2></div><a class="text-link" href="#library">Αναζήτηση και αποθηκευμένες ${icon('arrow')}</a></div>${words.length ? `<div class="vocabulary-grid">${words.map(word => `<div class="vocabulary-item"><strong lang="en">${escape(word.word)}</strong><span>${escape(word.translation)}</span><button class="round-button" data-speak="${escape(word.word)}" aria-label="Άκου: ${escape(word.word)}">${icon('voice')}</button></div>`).join('')}</div>` : '<div class="empty-state"><p>Η συλλογή σου ξεκινά με το πρώτο μάθημα.</p><a class="button button-primary" href="#dashboard">Κάνε την αρχή</a></div>'}<p class="small-note">Η λίστα δείχνει το υλικό των ολοκληρωμένων μαθημάτων. Δεν αποτελεί μέτρηση απομνημόνευσης ή πιστοποίηση γλωσσομάθειας. Η σειρά ημερών βασίζεται στην ώρα Αθήνας.</p></section></main>`);
}
let authEpoch = 0;
let settingsVisit = 0;
let accountSessions = null;
let teacherVisit = 0;
let teacherController = null;
function resetPrivateViews() {
  lessonVisit++; reviewVisit++; settingsVisit++; teacherVisit++;
  stopVoice();
  if (teacherController) { teacherController.abort(); teacherController = null; }
  state.lesson = null; state.review = null; state.teacher = null;
  pendingReviewAttempt = null; accountSessions = null; dashboardReview = null;
  state.assessment=null; state.support=null; state.notifications=null; state.admin=null; state.studyPlan=null; state.library=null; notificationSummary=null;
}
function accountMatches(userId,epoch) { return state.session.user?.id === userId && authEpoch === epoch; }
function loadAccountSessions() {
  const userId = state.session.user.id, epoch = authEpoch;
  if (accountSessions?.userId === userId) return;
  const entry = {userId,visit:++settingsVisit,loading:true,items:[],error:null};
  accountSessions = entry;
  api('account/sessions').then(data => {
    if (accountSessions !== entry || !accountMatches(userId,epoch)) return;
    Object.assign(entry,{loading:false,items:data.sessions,error:null});
    if (location.hash === '#settings') { const target=document.querySelector('#account-session-list'); if (target) target.innerHTML=accountSessionList(); }
  }).catch(error => {
    if (accountSessions !== entry || !accountMatches(userId,epoch)) return;
    Object.assign(entry,{loading:false,error:'Δεν μπορέσαμε να φορτώσουμε τις συνδέσεις σου. Δοκίμασε ξανά.'});
    if (location.hash === '#settings') { const target=document.querySelector('#account-session-list'); if (target) target.innerHTML=accountSessionList(); }
  });
}
function accountSessionList() {
  const entry = accountSessions;
  if (!entry || entry.loading) return '<p role="status" class="quiet">Φορτώνουμε τις ενεργές συνδέσεις σου…</p>';
  if (entry.error) return `<p class="form-error" role="alert">${escape(entry.error)}</p><button class="button button-outline" data-action="reload-sessions">Νέα προσπάθεια</button>`;
  return `<ul class="session-list">${entry.items.map(item=>`<li class="session-item"><div><strong>${item.current ? 'Αυτή η σύνδεση' : 'Άλλη σύνδεση'}</strong>${item.current ? '<span class="current-session-tag">Τρέχουσα</span>' : ''}<dl><div><dt>Έναρξη</dt><dd>${escape(athensDate(item.createdAt))}</dd></div><div><dt>Τελευταία χρήση</dt><dd>${escape(athensDate(item.lastSeenAt))}</dd></div><div><dt>Λήξη</dt><dd>${escape(athensDate(item.expiresAt))}</dd></div></dl></div>${!item.current ? `<button class="button button-small button-outline" data-action="revoke-session" data-session-id="${escape(item.id)}">Αποσύνδεση</button>` : ''}</li>`).join('')}</ul><p class="small-note">Οι ώρες εμφανίζονται σε ώρα Ελλάδας. Κάθε εγγραφή αντιστοιχεί σε σύνδεση browser, όχι σε αναγνωρισμένη φυσική συσκευή.</p>${entry.items.some(item=>!item.current) ? '<button class="button button-outline button-full" data-action="revoke-other-sessions">Αποσύνδεση όλων των άλλων συνδέσεων</button>' : '<p class="small-note">Δεν υπάρχουν άλλες ενεργές συνδέσεις.</p>'}`;
}
async function revokeAccountSession(button,others=false) {
  if (busy) return;
  busy = true; button.disabled = true;
  const userId = state.session.user.id, epoch = authEpoch;
  try {
    const result = await api(others ? 'account/sessions/revoke-others' : 'account/sessions',{method:others ? 'POST' : 'DELETE',body:others ? {} : {sessionId:button.dataset.sessionId}});
    if (!accountMatches(userId,epoch)) return;
    if (others && result.user) state.session = result;
    accountSessions = null;
    if (location.hash === '#settings') render();
    toast(others ? 'Οι άλλες ενεργές συνδέσεις αποσυνδέθηκαν.' : 'Η επιλεγμένη σύνδεση αποσυνδέθηκε.');
  } catch(error) { if (accountMatches(userId,epoch)) toast(error.message); }
  finally { busy=false; if (button.isConnected) button.disabled=false; }
}
function teacherMatches(visit,userId,epoch) { return teacherVisit === visit && state.teacher?.visit === visit && accountMatches(userId,epoch) && location.hash === '#teacher'; }
function beginTeacherStatus(preserve=false) {
  const previous=preserve ? state.teacher : null;
  if (teacherController) teacherController.abort();
  const visit=++teacherVisit,userId=state.session.user.id,epoch=authEpoch;
  const controller=new AbortController(); teacherController=controller;
  state.teacher={visit,loading:true,status:null,statusError:null,messages:previous?.messages || [],draft:previous?.draft || '',consent:previous?.consent || false,micConsent:previous?.micConsent || false,pending:false,error:null,board:previous?.board || null,lessonId:previous?.lessonId ?? state.curriculum.lessons.find(lesson=>!state.session.progress.completedLessons.includes(lesson.id))?.id ?? state.curriculum.lessons[0]?.id ?? ''};
  api('teacher/status',{signal:controller.signal}).then(status=>{
    if (!teacherMatches(visit,userId,epoch)) return;
    Object.assign(state.teacher,{loading:false,status}); render();
  }).catch(error=>{
    if (controller.signal.aborted || !teacherMatches(visit,userId,epoch)) return;
    Object.assign(state.teacher,{loading:false,statusError:'Δεν μπορέσαμε να ελέγξουμε τη διαθεσιμότητα του καθηγητή. Έλεγξε τη σύνδεση και δοκίμασε ξανά.'}); render();
  }).finally(()=>{ if (teacherController === controller) teacherController=null; });
}
function teacherBoard() {
  const board=state.teacher.board;
  if (!board) return `<div class="teacher-board-empty"><span class="feature-icon">${icon('book')}</span><h2>Ο πίνακας της συζήτησης.</h2><p>Όταν ο συνδεδεμένος καθηγητής επιλέξει ένα εκπαιδευτικό παράδειγμα, θα εμφανιστεί εδώ. Ο πίνακας δεν αλλάζει την πρόοδο ή τα ολοκληρωμένα μαθήματά σου.</p></div>`;
  const card=board.card;
  return `<div class="teacher-board-card"><span class="eyebrow">ΑΠΟ ΤΟ ΜΑΘΗΜΑ · ${escape(board.lesson.title)}</span>${card.image && /^\/assets\/[a-zA-Z0-9_.-]+\.svg$/.test(card.image) ? `<img src="${escape(card.image)}" alt="${escape(card.translation)}">` : `<span class="teacher-board-letters" aria-hidden="true">Aa</span>`}<h2 lang="en">${escape(card.word)}</h2><p>${escape(card.translation)}</p><div class="example-box"><strong lang="en">${escape(card.example)}</strong><span>${escape(card.exampleTranslation)}</span></div><button class="button button-outline button-small" data-action="teacher-board-speak">${icon('voice')} Άκου το παράδειγμα με φωνή browser</button></div>`;
}
function teacherView() {
  if (!state.teacher) beginTeacherStatus();
  const teacher=state.teacher, available=teacher.status?.available === true;
  return shell(`<main id="main-content" class="page teacher-page" tabindex="-1"><div class="page-intro"><span class="eyebrow">ΕΝΑΣ ΧΩΡΟΣ ΓΙΑ ΤΙΣ ΕΡΩΤΗΣΕΙΣ ΣΟΥ</span><h1>Ας το συζητήσουμε.</h1><p>Γραπτή συνομιλία με συνδεδεμένο τοπικό AI, όταν είναι διαθέσιμο. Οι απαντήσεις μπορεί να περιέχουν λάθη.</p></div><section id="teacher-status" class="teacher-status-card ${available ? 'available' : ''}" role="status"><span class="feature-icon">${icon(available ? 'check' : 'help')}</span><div><strong>${teacher.loading ? 'Ελέγχουμε τη διαθεσιμότητα…' : available ? 'Το τοπικό AI είναι διαθέσιμο.' : 'Το AI δεν είναι διαθέσιμο αυτή τη στιγμή.'}</strong><p>${escape(teacher.statusError || teacher.status?.message || 'Δεν αποστέλλονται μηνύματα μέχρι να ελέγξουμε τη σύνδεση.')}</p>${available ? `<span class="small-note">Τοπικός πάροχος${teacher.status?.model ? ` · ${escape(teacher.status.model)}` : ''} · Χωρίς συνεχή φωνητικό διάλογο</span>` : ''}</div>${!teacher.loading ? '<button class="button button-small button-outline" data-action="teacher-retry-status">Έλεγχος ξανά</button>' : ''}</section>${!available && !teacher.loading ? `<div class="info-box teacher-activation"><p><strong>Τα οργανωμένα μαθήματα λειτουργούν κανονικά.</strong> Δεν έχει ενεργοποιηθεί εναλλακτική υπηρεσία με χρέωση. Ο ιδιοκτήτης της εγκατάστασης μπορεί να συνδέσει ένα τοπικό μοντέλο Ollama στον server, αφού ελέγξει τους διαθέσιμους πόρους και τις οδηγίες στο README. Δεν εγκαθίσταται ή χρεώνεται μοντέλο από αυτή τη σελίδα. <a href="#dashboard">Συνέχισε τα μαθήματά σου →</a></p></div>` : ''}<div class="teacher-layout"><section class="chat-panel"><div class="chat-toolbar"><h2>Η συζήτησή σου</h2><button class="text-button" data-action="teacher-clear" ${teacher.pending ? 'disabled' : ''}>Καθαρισμός συζήτησης</button></div><div id="teacher-chat" class="teacher-chat" role="log" aria-live="polite" aria-relevant="additions">${teacher.messages.length ? teacher.messages.map((message,index)=>`<article class="chat-message ${message.role}"><span>${message.role === 'user' ? 'Εσύ' : 'Τοπικός καθηγητής AI'}</span><p>${escape(message.content)}</p>${message.role === 'assistant' ? `<button class="text-button" data-action="teacher-speak" data-message-index="${index}">${icon('voice')} Ακρόαση με φωνή browser</button>` : ''}</article>`).join('') : `<div class="chat-empty"><span aria-hidden="true">Aa</span><p>${available ? 'Ρώτησε κάτι για τα αγγλικά, για παράδειγμα: «Πότε βάζουμε a και πότε an;»' : 'Η συζήτηση θα ξεκινήσει όταν συνδεθεί και ενεργοποιηθεί τοπικό AI.'}</p></div>`}${teacher.pending ? `<article class="chat-message user pending"><span>Εσύ · αποστολή σε εξέλιξη</span><p>${escape(teacher.pendingText)}</p></article><p class="chat-waiting" role="status">Περιμένουμε απάντηση από το τοπικό μοντέλο…</p>` : ''}</div><p id="teacher-error" class="form-error" role="alert" ${teacher.error ? '' : 'hidden'}>${escape(teacher.error || '')}</p><form id="teacher-form" data-error-target="teacher-error"><label for="teacher-lesson">Θέμα συζήτησης<select id="teacher-lesson" ${teacher.pending ? 'disabled' : ''}><option value="">Γενική ερώτηση για τα αγγλικά</option>${state.curriculum.lessons.map(lesson=>`<option value="${escape(lesson.id)}" ${teacher.lessonId===lesson.id ? 'selected' : ''}>${escape(lesson.title)}</option>`).join('')}</select></label><p class="small-note context-note">Η αλλαγή θέματος καθαρίζει τη συζήτηση. Το θέμα δίνει στον καθηγητή τα σωστά παραδείγματα για τον πίνακα.</p><label for="teacher-message">Τι θα ήθελες να ρωτήσεις;<textarea id="teacher-message" name="message" maxlength="1000" rows="3" required placeholder="Γράψε την ερώτησή σου στα ελληνικά ή στα αγγλικά…" ${!available || teacher.pending ? 'disabled' : ''}>${escape(teacher.draft)}</textarea></label><label class="checkbox-label teacher-consent"><input type="checkbox" id="teacher-consent" ${teacher.consent ? 'checked' : ''} ${!available || teacher.pending ? 'disabled' : ''}><span>Επιτρέπω να σταλεί το κείμενό μου και έως 8 πρόσφατα μηνύματα στο συνδεδεμένο τοπικό AI. Η εφαρμογή δεν αποθηκεύει τη συζήτηση στη βάση της. Δεν θα γράψω κωδικούς ή ευαίσθητα προσωπικά δεδομένα.</span></label>${'SpeechRecognition' in window || 'webkitSpeechRecognition' in window ? `<label class="checkbox-label teacher-mic-consent"><input type="checkbox" id="teacher-mic-consent" ${teacher.micConsent ? 'checked' : ''} ${!available || teacher.pending ? 'disabled' : ''}><span>Προαιρετικά για μικρόφωνο: ο browser μπορεί να στείλει ήχο στον δικό του πάροχο αναγνώρισης. Η εφαρμογή δεν αποθηκεύει ήχο. Το κείμενο δεν αποστέλλεται στο AI μέχρι να πατήσω αποστολή.</span></label>` : ''}<div class="teacher-input-actions"><div>${'SpeechRecognition' in window || 'webkitSpeechRecognition' in window ? `<button type="button" class="round-button" data-action="teacher-mic" aria-label="Υπαγόρευση ερώτησης" ${!available || teacher.pending ? 'disabled' : ''}>${icon('mic')}</button>` : ''}<button type="button" class="button button-outline" data-action="teacher-stop">Διακοπή ${icon('voice')}</button></div><button class="button button-primary" type="submit" ${!available || teacher.pending || !teacher.consent ? 'disabled' : ''}>Αποστολή ${icon('arrow')}</button></div></form><p class="small-note">Η συνομιλία παραμένει μόνο σε αυτή την ανοιχτή σελίδα και καθαρίζεται όταν φύγεις. Η ακρόαση διαβάζει το κείμενο με φωνή του browser· δεν είναι ζωντανή φωνητική συνομιλία ούτε αξιολόγηση προφοράς.</p></section><section id="teacher-board" class="whiteboard teacher-live-board" aria-label="Πίνακας συζήτησης"><div class="board-topline"><span class="tiny-dot"></span> Ο ΠΙΝΑΚΑΣ ΤΗΣ ΣΥΖΗΤΗΣΗΣ</div>${teacherBoard()}</section></div><a class="text-link" href="#help">Βοήθεια και απόρρητο ${icon('help')}</a></main>`);
}
async function sendTeacherMessage() {
  const teacher=state.teacher;
  if (!teacher || teacher.pending || !teacher.status?.available) return;
  if (!teacher.consent) throw new Error('Διάβασε και επίλεξε πρώτα τη συγκατάθεση για τη γραπτή συζήτηση.');
  const message=String(document.querySelector('#teacher-message')?.value || '').trim();
  if (!message || message.length>1000) throw new Error('Γράψε μια ερώτηση έως 1.000 χαρακτήρες.');
  stopVoice();
  const visit=teacher.visit,userId=state.session.user.id,epoch=authEpoch;
  const history=teacher.messages.slice(-8).map(entry=>({role:entry.role,content:entry.content.slice(0,500)}));
  const controller=new AbortController(); teacherController=controller;
  Object.assign(teacher,{pending:true,pendingText:message,draft:message,error:null}); render();
  try {
    const reply=await api('teacher',{method:'POST',signal:controller.signal,body:{message,history,consent:true,...(teacher.lessonId ? {lessonId:teacher.lessonId} : {})}});
    if (!teacherMatches(visit,userId,epoch) || controller.signal.aborted) return;
    if (typeof reply.message !== 'string' || !reply.message.trim() || reply.mode !== 'local-ai') throw new Error('Το μοντέλο δεν επέστρεψε έγκυρη απάντηση. Δοκίμασε ξανά.');
    teacher.messages.push({role:'user',content:message},{role:'assistant',content:reply.message.slice(0,6000)});
    teacher.messages=teacher.messages.slice(-20);
    Object.assign(teacher,{pending:false,pendingText:null,draft:'',error:null});
    const action=reply.boardAction;
    if (action?.type === 'show_card' && typeof action.lessonId === 'string' && Number.isInteger(action.cardIndex)) {
      const lesson=state.curriculum.lessons.find(item=>item.id===action.lessonId);
      const card=lesson?.cards[action.cardIndex];
      if (card && action.cardIndex>=0) teacher.board={lesson,card};
    }
    render(); document.querySelector('#teacher-message')?.focus();
  } catch(error) {
    if (controller.signal.aborted || !teacherMatches(visit,userId,epoch)) return;
    Object.assign(teacher,{pending:false,pendingText:null,error:`Δεν λάβαμε επιβεβαιωμένη απάντηση. ${error.message} Το κείμενό σου παραμένει εδώ για νέα αποστολή.`}); render();
  } finally { if (teacherController===controller) teacherController=null; }
}
function stopTeacherWork() {
  stopVoice();
  if (teacherController) { teacherController.abort(); teacherController=null; }
  if (state.teacher?.loading) { Object.assign(state.teacher,{loading:false,statusError:'Ο έλεγχος σταμάτησε. Μπορείς να ελέγξεις ξανά τη διαθεσιμότητα.'}); render(); }
  if (state.teacher?.pending) {
    Object.assign(state.teacher,{pending:false,pendingText:null,error:'Η αναμονή σταμάτησε σε αυτή τη σελίδα. Το τοπικό μοντέλο μπορεί να έχει ήδη επεξεργαστεί το κείμενο. Δεν εμφανίστηκε απάντηση.'}); render();
  }
}

// Private feature responses are scoped to the account and exact route visit.
const featureHelpers={escape,icon,athensDate,renderExerciseExtras,renderExerciseInput};
let notificationSummary=null;
let featureSequence=0;
function newFeature(name,extra={}) {
  return state[name]={visit:++featureSequence,userId:state.session.user.id,epoch:authEpoch,route:location.hash,loading:true,error:null,...extra};
}
function featureMatches(name,entry) {
  return state[name]===entry && accountMatches(entry.userId,entry.epoch) && location.hash===entry.route;
}
function beginLibraryLoad() {
  const previous=state.library,view=newFeature('library',{data:null,query:previous?.query || '',bookmarkedOnly:previous?.bookmarkedOnly || false,pending:null,saveError:null});
  api('library').then(data=>{
    if(!featureMatches('library',view)) return;
    Object.assign(view,{loading:false,data}); render();
  }).catch(error=>{
    if(!featureMatches('library',view)) return;
    Object.assign(view,{loading:false,error:error.message}); render();
  });
}
function libraryView() {
  if(!state.library || !featureMatches('library',state.library)) beginLibraryLoad();
  return shell(renderLibraryPage(state.library,{escape,icon}));
}
function updateLibraryResults() {
  const view=state.library;
  if(!view || !featureMatches('library',view) || view.loading || view.error) return;
  const target=document.querySelector('#library-results');
  if(target) target.innerHTML=renderLibraryResults(view,{escape,icon});
  const count=document.querySelector('#library-result-count');
  if(count) count.textContent=`${filteredLibraryCards(view).length} από ${view.data.cards.length} λέξεις και φράσεις.`;
}
async function saveLibraryBookmark(button) {
  const view=state.library;
  if(!view || !featureMatches('library',view) || view.pending) return;
  const lessonId=button.dataset.lessonId,cardIndex=Number(button.dataset.cardIndex);
  const card=view.data.cards.find(item=>item.lessonId===lessonId && item.cardIndex===cardIndex);
  if(!card) return;
  const request={lessonId,cardIndex,bookmarked:!card.bookmarked};
  view.pending=request; view.saveError=null;
  document.querySelector('#library-save-error').hidden=true;
  document.querySelector('#library-save-status').hidden=false;
  document.querySelectorAll('.library-bookmark').forEach(node=>{node.disabled=true;});
  try {
    const data=await api('library/bookmarks',{method:'PUT',body:request});
    if(!accountMatches(view.userId,view.epoch)) return;
    if(!featureMatches('library',view)) { state.library=null; if(location.hash==='#library') render(); return; }
    Object.assign(view,{data,pending:null}); updateLibraryResults();
    document.querySelector('#library-saved-count').textContent=String(data.summary.bookmarkedCards);
    document.querySelector('#library-save-status').hidden=true;
    document.querySelector(`.library-bookmark[data-lesson-id="${CSS.escape(lessonId)}"][data-card-index="${cardIndex}"]`)?.focus({preventScroll:true});
    toast(request.bookmarked ? 'Η λέξη αποθηκεύτηκε στον λογαριασμό σου.' : 'Η λέξη αφαιρέθηκε από τις αποθηκευμένες.');
  } catch(error) {
    if(!accountMatches(view.userId,view.epoch)) return;
    if(!featureMatches('library',view)) { state.library=null; if(location.hash==='#library') render(); return; }
    view.pending=null; view.saveError='Δεν επιβεβαιώθηκε η αποθήκευση. '+error.message;
    const target=document.querySelector('#library-save-error'); target.textContent=view.saveError; target.hidden=false;
    document.querySelector('#library-save-status').hidden=true;
    document.querySelectorAll('.library-bookmark').forEach(node=>{node.disabled=false;});
  }
}
function invalidateStudyPlan(userId,epoch) {
  if(!accountMatches(userId,epoch)) return;
  state.studyPlan=null; dashboardReview=null;
  if(['#dashboard','#progress'].includes(location.hash)) render();
}
function beginStudyPlanLoad() {
  const view=newFeature('studyPlan',{data:null});
  api('study-plan').then(data=>{
    if(!featureMatches('studyPlan',view)) return;
    Object.assign(view,{loading:false,data}); render();
  }).catch(error=>{
    if(!featureMatches('studyPlan',view)) return;
    Object.assign(view,{loading:false,error:error.message}); render();
  });
}
function studyPlanView() {
  const view=state.studyPlan;
  if(!view || !featureMatches('studyPlan',view)) beginStudyPlanLoad();
  return state.studyPlan;
}
function updateNotificationBadge() {
  const badge=document.querySelector('#notification-badge'),bell=document.querySelector('#notification-bell');
  const count=notificationSummary?.unreadCount || 0;
  if(badge) { badge.hidden=!count; badge.textContent=String(Math.min(count,99)); }
  if(bell) bell.setAttribute('aria-label',count ? `Ειδοποιήσεις, ${count} αδιάβαστες` : 'Ειδοποιήσεις');
}
function loadNotificationSummary(force=false) {
  if(!state.session.user || (!force && notificationSummary?.userId===state.session.user.id)) return;
  const userId=state.session.user.id,epoch=authEpoch,entry={userId,unreadCount:0}; notificationSummary=entry;
  api('notifications').then(data=>{
    if(notificationSummary!==entry || !accountMatches(userId,epoch)) return;
    entry.unreadCount=data.unreadCount; updateNotificationBadge();
  }).catch(()=>{ if(notificationSummary===entry && accountMatches(userId,epoch)) updateNotificationBadge(); });
}
function beginAssessmentLoad() {
  const view=newFeature('assessment',{catalog:null,attempt:null,result:null,index:0,answers:[],pending:null});
  api('assessments').then(data=>{
    if(!featureMatches('assessment',view)) return;
    Object.assign(view,{loading:false,catalog:data}); render();
  }).catch(error=>{
    if(!featureMatches('assessment',view)) return;
    Object.assign(view,{loading:false,error:error.message}); render();
  });
}
function assessmentView() {
  if(!state.assessment) beginAssessmentLoad();
  return shell(renderAssessmentPage(state.assessment,featureHelpers));
}
async function startAssessment(kind,unitId,button) {
  if(busy) return;
  const view=state.assessment; busy=true; button.disabled=true;
  view.error=null;
  try {
    const attempt=await api('assessments/start',{method:'POST',body:{kind,...(kind==='unit' ? {unitId} : {})}});
    if(!featureMatches('assessment',view)) return;
    Object.assign(view,{attempt,result:null,index:0,answers:[],pending:null}); render();
  } catch(error) {
    if(featureMatches('assessment',view)) { view.error=error.message; render(); }
  } finally { busy=false; if(button.isConnected) button.disabled=false; }
}
async function submitAssessment() {
  const view=state.assessment;
  if(!view?.attempt || view.result) return;
  if(!view.pending) {
    view.answers[view.index]=String(document.querySelector('#assessment-answer')?.value || '').trim();
    if(!view.answers[view.index]) throw new Error('Γράψε πρώτα την απάντησή σου.');
    if(view.index<view.attempt.questions.length-1) { view.index++; view.error=null; render(); return; }
    // Keep exactly this payload after an uncertain response: server submission is idempotent.
    view.pending=Object.freeze({attemptId:view.attempt.attemptId,answers:Object.freeze(view.attempt.questions.map((question,index)=>Object.freeze({questionId:question.id,answer:view.answers[index] || ''})))});
  }
  const payload=view.pending; view.error=null;
  render();
  const retry=document.querySelector('[data-action="assessment-submit-retry"]');
  if(retry) { retry.disabled=true; retry.textContent='Υποβάλλουμε τη δοκιμή…'; }
  try {
    const result=await api('assessments/submit',{method:'POST',body:payload});
    if(!featureMatches('assessment',view)) return;
    Object.assign(view,{result,pending:null,error:null}); render();
  } catch(error) {
    if(!featureMatches('assessment',view)) return;
    view.error=error.status===410 ? error.message : `Η υποβολή δεν επιβεβαιώθηκε. ${error.message} Η νέα προσπάθεια στέλνει ακριβώς τις ίδιες απαντήσεις.`;
    view.expired=error.status===410; render();
  }
}
function beginSupportLoad() {
  const id=location.hash.split('/')[1],view=newFeature('support',{ticket:null,tickets:[]});
  const path=id ? `support/tickets/${encodeURIComponent(id)}` : 'support/tickets';
  api(path).then(data=>{
    if(!featureMatches('support',view)) return;
    Object.assign(view,{loading:false,ticket:data.ticket || null,tickets:data.tickets || []}); render();
  }).catch(error=>{
    if(!featureMatches('support',view)) return;
    Object.assign(view,{loading:false,error:error.message,failed:true}); render();
  });
}
function supportView() {
  if(!state.support || state.support.route!==location.hash) beginSupportLoad();
  return shell(renderSupportPage(state.support,featureHelpers));
}
async function saveSupport(form,data) {
  const view=state.support;
  const reply=form.id==='support-reply-form';
  const result=await api(reply ? `support/tickets/${encodeURIComponent(view.ticket.id)}/messages` : 'support/tickets',{method:'POST',body:reply ? {message:data.message} : data});
  if(!featureMatches('support',view)) return;
  if(reply) { view.ticket=result.ticket; view.error=null; render(); }
  else go(`support/${result.ticket.id}`);
  toast(reply ? 'Η απάντησή σου αποθηκεύτηκε.' : 'Το αίτημά σου αποθηκεύτηκε σε αυτή την εγκατάσταση.');
}
async function changeSupportStatus(status) {
  const view=state.support;
  const result=await api(`support/tickets/${encodeURIComponent(view.ticket.id)}`,{method:'PUT',body:{status}});
  if(!featureMatches('support',view)) return;
  view.ticket=result.ticket; view.error=null; render();
}
function beginNotificationsLoad() {
  const view=newFeature('notifications',{items:[],unreadCount:0,preferences:{reminders:true,achievements:true,supportReplies:true}});
  Promise.all([api('notifications'),api('notifications/preferences')]).then(([data,prefs])=>{
    if(!featureMatches('notifications',view)) return;
    Object.assign(view,data,{preferences:prefs.preferences,loading:false});
    notificationSummary={userId:view.userId,unreadCount:data.unreadCount}; render();
  }).catch(error=>{
    if(!featureMatches('notifications',view)) return;
    Object.assign(view,{loading:false,error:error.message,failed:true}); render();
  });
}
function notificationsView() {
  if(!state.notifications) beginNotificationsLoad();
  return shell(renderNotificationsPage(state.notifications,featureHelpers));
}
async function readNotifications(id) {
  const view=state.notifications;
  const data=await api('notifications/read',{method:'POST',body:id ? {ids:[id]} : {all:true}});
  if(!featureMatches('notifications',view)) return;
  Object.assign(view,data,{error:null}); notificationSummary={userId:view.userId,unreadCount:data.unreadCount}; render();
}
function selectAdminLesson(view,record,{copy=false}={}) {
  const lesson=structuredClone(record.draft || record.published);
  if(copy) lesson.id=`${lesson.id.slice(0,54)}-copy`;
  Object.assign(view,{selectedId:record.id,creating:copy,editorJson:JSON.stringify(lesson,null,2),editorDirty:false,preview:null,error:null});
}
function beginAdminLoad() {
  const tab=state.admin?.tab || 'overview';
  const view=newFeature('admin',{tab,overview:null,lessons:[],allowedImages:[],selectedId:null,editorJson:'',editorDirty:false,preview:null,creating:false,supportLoading:false,supportTickets:[],supportTotal:0,supportOffset:0,supportFilter:'open',supportTicket:null});
  Promise.all([api('admin/overview'),api('admin/curriculum')]).then(([overview,data])=>{
    if(!featureMatches('admin',view)) return;
    Object.assign(view,{overview,lessons:data.lessons,allowedImages:data.allowedImages,notice:data.contentReviewNotice,loading:false});
    if(data.lessons.length) selectAdminLesson(view,data.lessons[0]);
    if(view.tab==='support') loadAdminSupport(view);
    render();
  }).catch(error=>{
    if(!featureMatches('admin',view)) return;
    Object.assign(view,{loading:false,error:error.message,forbidden:error.status===403}); render();
  });
}
function adminView() {
  if(state.session.user.isAdmin!==true) return shell('<main id="main-content" class="page" tabindex="-1"><h1>Δεν έχεις πρόσβαση στη διαχείριση.</h1><p>Αυτή η σελίδα αφορά εξουσιοδοτημένους διαχειριστές.</p><a class="button button-primary" href="#dashboard">Στη διαδρομή μου</a></main>');
  if(!state.admin) beginAdminLoad();
  return shell(renderAdminPage(state.admin,featureHelpers));
}
function parseAdminEditor() {
  const text=document.querySelector('#admin-lesson-json')?.value || state.admin.editorJson;
  if(new TextEncoder().encode(text).length>60000) throw new Error('Το JSON είναι πολύ μεγάλο. Χώρισε το περιεχόμενο σε μικρότερα μαθήματα.');
  let lesson;
  try { lesson=JSON.parse(text); } catch { throw new Error('Το JSON δεν είναι έγκυρο. Έλεγξε εισαγωγικά, κόμματα και αγκύλες.'); }
  if(!lesson || typeof lesson!=='object' || Array.isArray(lesson)) throw new Error('Το περιεχόμενο χρειάζεται ένα αντικείμενο μαθήματος JSON.');
  return lesson;
}
function setAdminEditorValue(lesson,{rerender=false}={}) {
  const view=state.admin;
  Object.assign(view,{editorJson:JSON.stringify(lesson,null,2),editorDirty:true,preview:null,error:null});
  const textarea=document.querySelector('#admin-lesson-json');
  if(textarea) textarea.value=view.editorJson;
  const publish=document.querySelector('[data-action="admin-publish"]'); if(publish) publish.disabled=true;
  document.querySelector('#admin-preview')?.remove();
  if(rerender) render();
}
function changeAdminField(input) {
  const path=input.dataset.lessonField;
  if(!path || !/^(id|title|subtitle|objective|xp|durationMinutes|cards\.\d+\.(word|translation|pronunciation|example|exampleTranslation|image)|exercises\.\d+\.(id|type|prompt|answer|acceptedAnswers|explanation|hint|choices|tokens|passage|audioText))$/.test(path)) return;
  const view=state.admin,record=view.lessons.find(item=>item.id===view.selectedId);
  if(!view.creating && record && !record.canChangeExercises) return;
  try {
    const lesson=JSON.parse(view.editorJson),parts=path.split('.'),key=parts.pop();
    let owner=lesson; for(const part of parts) owner=owner[part];
    if(!owner || typeof owner!=='object') return;
    const old=owner[key];
    if(['acceptedAnswers','choices','tokens'].includes(key)) owner[key]=input.value.split('\n').map(item=>item.trim()).filter(Boolean);
    else if(['xp','durationMinutes'].includes(key)) owner[key]=Number(input.value);
    else if(['pronunciation','example','exampleTranslation','image','hint'].includes(key) && !input.value.trim()) delete owner[key];
    else owner[key]=input.value;
    if(key==='answer') {
      owner.acceptedAnswers=[input.value,...(Array.isArray(owner.acceptedAnswers) ? owner.acceptedAnswers.filter(answer=>answer!==old && answer!==input.value) : [])];
      const accepted=document.querySelector(`[data-lesson-field="${parts.join('.')}.acceptedAnswers"]`);
      if(accepted) accepted.value=owner.acceptedAnswers.join('\n');
    }
    if(key==='type') {
      for(const extra of ['choices','tokens','passage','audioText']) delete owner[extra];
      if(owner.type==='multiple-choice') owner.choices=[owner.answer || 'Hello','Goodbye'];
      if(owner.type==='word-order') owner.tokens=(owner.answer || 'Hello there').split(/\s+/);
      if(owner.type==='reading') owner.passage='Hello, Anna. My name is Alex.';
      if(owner.type==='listening') owner.audioText=owner.answer || 'Hello';
    }
    setAdminEditorValue(lesson,{rerender:key==='type'});
  } catch { formError(new Error('Διόρθωσε πρώτα το JSON στις προχωρημένες επιλογές.'),'admin-error'); }
}
function changeAdminRows(action,index) {
  if(busy) return;
  const view=state.admin,record=view.lessons.find(item=>item.id===view.selectedId);
  if(!view.creating && record && !record.canChangeExercises) return;
  try {
    const lesson=parseAdminEditor();
    if(action==='admin-add-card' && lesson.cards.length<12) lesson.cards.push({word:'Hello',translation:'Γεια σου',example:'Hello, Anna.',exampleTranslation:'Γεια σου, Άννα.'});
    else if(action==='admin-add-exercise' && lesson.exercises.length<20) lesson.exercises.push({id:`exercise-${lesson.exercises.length+1}`,type:'translate',prompt:'Πώς λέμε «Γεια σου»;',answer:'Hello',acceptedAnswers:['Hello'],explanation:'Hello σημαίνει γεια σου.'});
    else if(action==='admin-remove-card' && Number.isInteger(index) && index>=0 && index<lesson.cards.length && lesson.cards.length>1) lesson.cards.splice(index,1);
    else if(action==='admin-remove-exercise' && Number.isInteger(index) && index>=0 && index<lesson.exercises.length && lesson.exercises.length>1) lesson.exercises.splice(index,1);
    setAdminEditorValue(lesson,{rerender:true});
  } catch(error) { formError(error,'admin-error'); }
}
function updateAdminRecord(view,record) {
  const index=view.lessons.findIndex(item=>item.id===record.id);
  if(index<0) view.lessons.push(record); else view.lessons[index]=record;
  selectAdminLesson(view,record);
}
async function saveAdminDraft() {
  const view=state.admin,lesson=parseAdminEditor(),record=view.lessons.find(item=>item.id===view.selectedId);
  const result=await api(view.creating ? 'admin/lessons' : `admin/lessons/${encodeURIComponent(record.id)}`,{method:view.creating ? 'POST' : 'PUT',body:{lesson,baseVersion:view.creating ? 0 : record.version}});
  if(!featureMatches('admin',view)) return;
  updateAdminRecord(view,result.lesson);
  view.overview.counts.draftLessons=view.lessons.filter(item=>item.draft).length;
  render(); toast('Το προσχέδιο αποθηκεύτηκε. Για να το δουν οι μαθητές, χρειάζεται δημοσίευση.');
}
async function publishAdminDraft() {
  const view=state.admin,record=view.lessons.find(item=>item.id===view.selectedId);
  if(!record?.draft || view.editorDirty || view.creating) return;
  const result=await api(`admin/lessons/${encodeURIComponent(record.id)}/publish`,{method:'POST',body:{baseVersion:record.version}});
  if(!featureMatches('admin',view)) return;
  updateAdminRecord(view,result.lesson);
  view.overview.counts.draftLessons=view.lessons.filter(item=>item.draft).length;
  // Refresh the same public view students will receive after publication.
  try {
    const curriculum=await api('curriculum');
    if(!featureMatches('admin',view)) return;
    state.curriculum=curriculum; view.overview.counts.publishedLessons=curriculum.lessons.length;
  } catch(error) { if(featureMatches('admin',view)) view.error=`Το μάθημα δημοσιεύτηκε. Η τοπική λίστα δεν ανανεώθηκε: ${error.message}`; }
  if(!featureMatches('admin',view)) return;
  render(); toast('Το αποθηκευμένο προσχέδιο δημοσιεύτηκε στη διαδρομή.');
}
async function loadAdminSupport(view=state.admin,id=null) {
  if(!view) return;
  const token=++featureSequence; view.supportRequest=token; view.supportLoading=true; view.error=null;
  try {
    const data=await api(id ? `admin/support/tickets/${encodeURIComponent(id)}` : `admin/support/tickets?status=${encodeURIComponent(view.supportFilter)}&offset=${view.supportOffset}&limit=20`);
    if(!featureMatches('admin',view) || view.supportRequest!==token) return;
    Object.assign(view,id ? {supportTicket:data.ticket} : {supportTicket:null,supportTickets:data.tickets,supportTotal:data.total});
    view.supportLoading=false; if(view.tab==='support') render();
  } catch(error) {
    if(!featureMatches('admin',view) || view.supportRequest!==token || view.tab!=='support') return;
    view.supportLoading=false; view.error=error.message;
    if(error.status===403) Object.assign(view,{forbidden:true,overview:null,lessons:[],supportTickets:[],supportTicket:null});
    render();
  }
}
async function replyAdminSupport(data) {
  const view=state.admin;
  const result=await api(`admin/support/tickets/${encodeURIComponent(view.supportTicket.id)}/messages`,{method:'POST',body:{message:data.message}});
  if(!featureMatches('admin',view)) return;
  view.supportTicket=result.ticket; view.error=null; render(); toast('Η απάντηση αποθηκεύτηκε στο αίτημα του μαθητή.');
}
async function featureAction(button,callback,errorId) {
  if(busy) return;
  busy=true; button.disabled=true; button.setAttribute('aria-busy','true');
  const route=location.hash,userId=state.session.user?.id,epoch=authEpoch;
  try { await callback(); }
  catch(error) {
    if(location.hash===route && accountMatches(userId,epoch)) formError(error,errorId);
  } finally { busy=false; if(button.isConnected) { button.disabled=false; button.removeAttribute('aria-busy'); } }
}
function settings() {
  loadEmailStatus();
  const profile = state.session.profile;
  loadAccountSessions();
  return shell(`<main id="main-content" class="page settings-page" tabindex="-1"><div class="page-intro"><span class="eyebrow">Ο ΧΩΡΟΣ ΣΟΥ, ΟΙ ΕΠΙΛΟΓΕΣ ΣΟΥ</span><h1>Όπως σε βολεύει.</h1><p>Ρύθμισε τον στόχο σου και διαχειρίσου τον λογαριασμό και τις συνδέσεις σου.</p></div><div class="settings-grid"><div><section class="side-card"><h2>Ο λογαριασμός σου</h2><form id="account-profile-form" data-error-target="account-profile-error"><label>Όνομα<input name="name" value="${escape(state.session.user.name)}" autocomplete="given-name" minlength="2" maxlength="80" required></label><label>Email<input type="email" value="${escape(state.session.user.email)}" readonly></label><p id="account-profile-error" class="form-error" role="alert" hidden></p><button class="button button-primary" type="submit">Αποθήκευση ονόματος ${icon('check')}</button></form></section>${renderAccountEmail(state.session.user,emailStatus,{escape})}<section class="side-card"><h2>Η καθημερινή σου αρχή</h2><form id="settings-form"><label>Ημερήσιος στόχος<select name="goalMinutes">${[5,10,15].map(goal=>`<option value="${goal}" ${profile.goalMinutes===goal ? 'selected' : ''}>${goal} λεπτά</option>`).join('')}</select></label><p id="form-error" class="form-error" role="alert" hidden></p><button class="button button-primary" type="submit">Αποθήκευση στόχου ${icon('check')}</button></form><p class="small-note">Ο στόχος είναι προσωπική προτίμηση. Δεν μετράμε ακόμη ενεργά λεπτά μελέτης.</p></section><section class="side-card"><h2>Αλλαγή κωδικού</h2><form id="password-form" data-error-target="password-error"><label>Τρέχων κωδικός<input type="password" name="currentPassword" autocomplete="current-password" required maxlength="256"></label><label>Νέος κωδικός<input type="password" name="newPassword" autocomplete="new-password" required minlength="10" maxlength="256" placeholder="Τουλάχιστον 10 χαρακτήρες"></label><label>Επιβεβαίωση νέου κωδικού<input type="password" name="confirmPassword" autocomplete="new-password" required minlength="10" maxlength="256"></label><p id="password-error" class="form-error" role="alert" hidden></p><button class="button button-primary" type="submit">Αλλαγή κωδικού ${icon('lock')}</button></form><p class="small-note">Με την αλλαγή κωδικού αποσυνδέονται όλες οι άλλες συνδέσεις σου. Η τρέχουσα σύνδεση ανανεώνεται.</p></section><section id="account-sessions" class="side-card"><div class="section-heading"><h2>Ενεργές συνδέσεις</h2><button class="text-button" data-action="reload-sessions">Ανανέωση συνδέσεων</button></div><div id="account-session-list">${accountSessionList()}</div><button class="button button-outline settings-logout" data-action="logout">${icon('logout')} Αποσύνδεση από εδώ</button></section></div><div><section class="side-card"><h2>Η εμφάνισή σου</h2><label>Θέμα εφαρμογής<select id="theme-select"><option value="light">Φωτεινό</option><option value="dark" ${document.documentElement.dataset.theme === 'dark' ? 'selected' : ''}>Σκοτεινό</option></select></label><p class="small-note">Το θέμα αποθηκεύεται μόνο σε αυτόν τον browser. Οι ρυθμίσεις μειωμένης κίνησης της συσκευής σου εφαρμόζονται αυτόματα.</p>${installPrompt ? '<button class="button button-outline" data-action="install">Εγκατάσταση εφαρμογής</button>' : '<p class="small-note">Για εγκατάσταση, δες την επιλογή «Εγκατάσταση εφαρμογής» ή «Προσθήκη στην αρχική οθόνη» στο μενού του browser, εφόσον υποστηρίζεται.</p>'}</section><section class="side-card data-card"><h2>Τα δεδομένα σου</h2><p>Ο λογαριασμός και η πρόοδός σου αποθηκεύονται στη βάση δεδομένων αυτής της εγκατάστασης.</p><button class="button button-outline" data-action="export">${icon('download')} Εξαγωγή δεδομένων</button><button class="text-button danger-link" data-action="show-delete">Διαγραφή λογαριασμού</button><form id="delete-form" class="delete-form" hidden><p>Η διαγραφή αφαιρεί μόνιμα τον λογαριασμό και την πρόοδό σου από την ενεργή βάση. Δεν αναιρείται. Τυχόν αντίγραφα ασφαλείας χρειάζονται χωριστή πολιτική διατήρησης πριν από δημόσια χρήση.</p><label>Επιβεβαίωσε τον κωδικό σου<input type="password" name="password" autocomplete="current-password" required maxlength="256"></label><p id="delete-error" class="form-error" role="alert" hidden></p><button class="button button-danger" type="submit">Οριστική διαγραφή</button><button class="text-button" type="button" data-action="cancel-delete">Ακύρωση</button></form></section><section class="side-card settings-shortcuts"><h2>Περισσότερα για την εξάσκησή σου</h2><a class="text-link" href="#library">Το προσωπικό μου λεξικό ${icon('book')}</a><a class="text-link" href="#assessments">Δοκιμές γνώσεων ${icon('book')}</a><a class="text-link" href="#notifications">Ειδοποιήσεις και προτιμήσεις ${icon('bell')}</a><a class="text-link" href="#support">Τα αιτήματα υποστήριξής μου ${icon('help')}</a>${state.session.user.isAdmin === true ? '<a class="text-link" href="#admin">Διαχείριση εγκατάστασης</a>' : ''}</section><section class="side-card"><h2>Χρειάζεσαι βοήθεια;</h2><p>Δες πώς λειτουργεί η δοκιμή και τι ισχύει για τα δεδομένα σου.</p><a class="text-link" href="#help">Βοήθεια ${icon('help')}</a><nav class="legal-links" aria-label="Πληροφορίες εφαρμογής"><a href="#privacy">Απόρρητο</a><a href="#terms">Όροι χρήσης</a></nav></section></div></div></main>`);
}
function help() {
  return shell(`<main id="main-content" class="page help-page ${!state.session?.user ? 'wrap' : ''}" tabindex="-1"><div class="page-intro"><span class="eyebrow">ΜΕ ΚΑΘΑΡΑ ΛΟΓΙΑ</span><h1>Μερικές χρήσιμες απαντήσεις.</h1><p>Τι μπορείς να δοκιμάσεις σήμερα και τι ετοιμάζουμε για αργότερα.</p></div><div class="help-grid"><section class="side-card"><h2>Η δοκιμαστική εφαρμογή</h2><details open><summary>Τι περιλαμβάνει;</summary><p>Εγγραφή ενηλίκων, επιλογή Ελληνικά → Αγγλικά, ${state.curriculum.lessons.length} μικρά μαθήματα A1, συνέχιση από την τελευταία αποθηκευμένη θέση, προγραμματισμένες επαναλήψεις, σύντομες δοκιμές εισαγωγικών γνώσεων, αιτήματα υποστήριξης, ειδοποιήσεις μέσα στην εφαρμογή, γραπτές ασκήσεις, προαιρετική φωνητική εισαγωγή και αποθήκευση προόδου στον λογαριασμό. Μπορείς να συνδεθείς από άλλον browser στην ίδια εγκατάσταση.</p></details><details><summary>Ο καθηγητής είναι πραγματικό AI;</summary><p>Οι εξηγήσεις μέσα στα οργανωμένα μαθήματα είναι προκαθορισμένες. Υπάρχει ξεχωριστή σελίδα καθηγητή AI που λειτουργεί μόνο αν ο ιδιοκτήτης συνδέσει διαθέσιμο τοπικό μοντέλο. Αν δεν υπάρχει, το δηλώνουμε καθαρά και τα μαθήματα συνεχίζουν κανονικά. Δεν υπάρχει συνεχής φωνητικός διάλογος ή αξιολόγηση προφοράς.</p></details><details><summary>Πώς λειτουργεί η φωνή;</summary><p>Το κουμπί ακρόασης χρησιμοποιεί τις διαθέσιμες φωνές της συσκευής ή του browser. Η αναγνώριση ομιλίας είναι προαιρετική και ζητά ξεχωριστή συγκατάθεση και άδεια μικροφώνου. Ο browser μπορεί να στείλει τον ήχο σε δικό του πάροχο. Δεν αποθηκεύουμε ήχο· η αναγνωρισμένη απάντηση γίνεται κείμενο. Δεν λειτουργεί σε όλους τους browsers και χρειάζεται ασφαλή σύνδεση ή localhost. Η πληκτρολόγηση παραμένει διαθέσιμη.</p></details><details><summary>Υπάρχει συνδρομή ή χρέωση;</summary><p>Δεν υπάρχουν ενεργές πληρωμές, δωρεάν δοκιμή με αυτόματη χρέωση ή συνδρομές σε αυτή την εγκατάσταση. Η δοκιμαστική εφαρμογή δεν ζητά κάρτα.</p></details><details><summary>Τι γίνεται χωρίς σύνδεση;</summary><p>Ορισμένα δημόσια αρχεία της εφαρμογής αποθηκεύονται προσωρινά στον browser. Η σύνδεση, τα μαθήματα και η αποθήκευση προόδου χρειάζονται διαθέσιμο διακομιστή. Αν χαθεί η σύνδεση στην ολοκλήρωση, κράτησε ανοιχτή τη σελίδα και ξαναδοκίμασε.</p></details></section><section class="side-card"><h2>Απόρρητο & λογαριασμός</h2><details open><summary>Ποια δεδομένα κρατά η εφαρμογή;</summary><p>Όνομα, email, προστατευμένη σύνοψη κωδικού, εκπαιδευτικές προτιμήσεις, ολοκληρωμένα μαθήματα, τελευταία αποθηκευμένη θέση και σωστές απαντήσεις εντός μαθήματος, πρόγραμμα επαναλήψεων και αποτελέσματα προσπαθειών επανάληψης, αποτελέσματα των δοκιμών γνώσεων, αιτήματα και μηνύματα υποστήριξης, ειδοποιήσεις και προτιμήσεις τους, πόντοι και δραστηριότητα για τη σειρά ημερών. Μεταδεδομένα συνεδριών κρατούν την έναρξη, τελευταία χρήση και λήξη σύνδεσης. Η προαιρετική συζήτηση AI στέλνεται στο συνδεδεμένο τοπικό μοντέλο και δεν αποθηκεύεται στη βάση της εφαρμογής. Ένα απαραίτητο cookie κρατά τη σύνδεσή σου. Το θέμα αποθηκεύεται τοπικά. Δεν υπάρχουν analytics ή διαφημιστικά cookies.</p></details><details><summary>Πώς εξάγω ή διαγράφω τα δεδομένα μου;</summary><p>Από τις Ρυθμίσεις μπορείς να κατεβάσεις αντίγραφο δεδομένων σε JSON και να διαγράψεις τον λογαριασμό σου με επιβεβαίωση κωδικού. Η αποσύνδεση αφαιρεί τη συνεδρία του τρέχοντος browser. Μπορείς επίσης να αλλάξεις όνομα και κωδικό και να αποσυνδέσεις άλλες συνδέσεις από τις Ρυθμίσεις. Η επιβεβαίωση και η αλλαγή email γίνονται από τις Ρυθμίσεις, όταν η εγκατάσταση διαθέτει ενεργή αποστολή email. Η νέα διεύθυνση ισχύει μόνο μετά την επιβεβαίωσή της.</p></details><details><summary>Μπορούν να τη χρησιμοποιήσουν παιδιά;</summary><p>Η παρούσα δοκιμή προορίζεται μόνο για ενηλίκους 18+. Πριν υποστηριχθούν ανήλικοι απαιτούνται κατάλληλες γονικές διαδικασίες, νομικός έλεγχος και πρόσθετη προστασία.</p></details><details><summary>Ξέχασα τον κωδικό μου.</summary><p>Άνοιξε την <a href="#reset-request">ανάκτηση κωδικού</a>. Η σελίδα δείχνει αν είναι διαθέσιμη η αποστολή email. Χρειάζεσαι πρόσβαση στο email σου και νέο σύνδεσμο που δεν έχει λήξει ή χρησιμοποιηθεί. Μην χρησιμοποιείς κωδικό που χρησιμοποιείς αλλού.</p></details><details><summary>Πώς αναφέρω ένα πρόβλημα;</summary><p>Δεν έχει οργανωθεί ακόμη ανθρώπινο τμήμα υποστήριξης. Με σύνδεση στον λογαριασμό σου, μπορείς να καταγράψεις τη σελίδα, τα βήματα και το μήνυμα σφάλματος στα <a href="#support">αιτήματα υποστήριξης</a>. Αποθηκεύονται ιδιωτικά σε αυτή την εγκατάσταση. Δεν αποστέλλεται email και δεν υπάρχει εγγυημένος χρόνος απάντησης. Μην στέλνεις κωδικούς ή προσωπικές ηχογραφήσεις.</p></details><div class="info-box"><p>Η παρούσα ενημέρωση περιγράφει τη δοκιμή. Δες την <a href="#privacy">ενημέρωση απορρήτου</a> και το <a href="#terms">προσχέδιο όρων χρήσης</a>. Χρειάζονται νομικό έλεγχο πριν από δημόσια κυκλοφορία.</p></div></section></div></main>`);
}
function render() {
  if (!state.session || !state.curriculum) return;
  stopVoice();
  let route = location.hash.slice(1) || 'home';
  const routeKind=route.split('/')[0];
  const isMailRoute=mailKinds.includes(routeKind);
  const publicRoutes = ['home','login','register','help','privacy','terms'];
  if (!state.session.user && !publicRoutes.includes(route) && !isMailRoute) { go('login'); return; }
  if (state.session.user && ['home','login','register'].includes(route)) { go(state.session.profile ? 'dashboard' : 'onboarding'); return; }
  if (state.session.user && !state.session.profile && !['onboarding','help','privacy','terms'].includes(route) && !isMailRoute) { go('onboarding'); return; }
  if(!isMailRoute && mailView) { mailView.token=''; mailView=null; }
  if (!route.startsWith('lesson/') && state.lesson) { state.lesson = null; lessonVisit++; }
  if ((!['review','review/mistakes'].includes(route) || state.review?.route!==location.hash) && state.review) { state.review = null; reviewVisit++; }
  if (route !== 'settings' && accountSessions) { accountSessions=null; settingsVisit++; }
  if (route !== 'teacher' && state.teacher) { teacherVisit++; if (teacherController) { teacherController.abort(); teacherController=null; } state.teacher=null; }
  if(!['dashboard','progress'].includes(route)) state.studyPlan=null;
  if(route!=='library') state.library=null;
  if(route!=='assessments') state.assessment=null;
  if(route!=='support' && !route.startsWith('support/')) state.support=null;
  if(route!=='notifications') state.notifications=null;
  if(route!=='admin') state.admin=null;
  if(state.session.user && route!=='notifications') loadNotificationSummary();
  let content;
  if (route === 'home') content = landing();
  else if (['login','register'].includes(route)) content = auth(route);
  else if (isMailRoute) { if(routeKind==='reset-request') loadEmailStatus(); content=shell(renderMailPage(currentMailView(route),emailStatus,{escape},{signedIn:!!state.session.user})); }
  else if (['privacy','terms'].includes(route)) content=shell(renderLegalPage(route,{signedIn:!!state.session.user}));
  else if (route === 'onboarding') content = onboarding();
  else if (route === 'dashboard') content = dashboard();
  else if (['review','review/mistakes'].includes(route)) content = reviewView();
  else if (route === 'library') content = libraryView();
  else if (route === 'progress') content = progressView();
  else if (route === 'settings') content = settings();
  else if (route === 'teacher') content = teacherView();
  else if (route === 'assessments') content = assessmentView();
  else if (route === 'support' || route.startsWith('support/')) content = supportView();
  else if (route === 'notifications') content = notificationsView();
  else if (route === 'admin') content = adminView();
  else if (route === 'help') content = help();
  else if (route.startsWith('lesson/')) content = lessonView(route.split('/')[1]);
  else content = shell('<main class="page" id="main-content"><h1>Αυτή η σελίδα δεν βρέθηκε.</h1><a href="#home">Πίσω στην αρχή</a></main>');
  app.innerHTML = content;
  updateNotificationBadge();
  const page = document.querySelector('#main-content');
  page?.focus({preventScroll:true});
  window.scrollTo({top:0,behavior:'instant'});
  document.title = `${route.startsWith('lesson/') ? state.lesson?.title || 'Μάθημα' : ({dashboard:'Η διαδρομή μου',progress:'Η πρόοδός μου',settings:'Ρυθμίσεις',review:'Επαναλήψεις',library:'Το προσωπικό μου λεξικό',teacher:'Καθηγητής AI',assessments:'Δοκιμές γνώσεων',support:'Αιτήματα υποστήριξης',notifications:'Ειδοποιήσεις',admin:'Διαχείριση',help:'Βοήθεια',privacy:'Απόρρητο',terms:'Όροι χρήσης', 'reset-request':'Ανάκτηση κωδικού','reset-password':'Νέος κωδικός','verify-email':'Επιβεβαίωση email','confirm-email-change':'Αλλαγή email',login:'Σύνδεση',register:'Εγγραφή',onboarding:'Η δική σου αρχή'}[route.split('/')[0]] || 'Η δική σου νέα αρχή')} — AI Language Academy`;
}
function speak(text,lang='en-GB') {
  if (!('speechSynthesis' in window)) { toast('Η ακρόαση δεν υποστηρίζεται σε αυτόν τον browser. Δες το γραπτό παράδειγμα.'); return; }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = lang; utterance.rate = 0.82;
  const voice = window.speechSynthesis.getVoices().find(item => item.lang === lang) || window.speechSynthesis.getVoices().find(item => item.lang.startsWith(lang.slice(0,2)));
  if (voice) utterance.voice = voice;
  utterance.onerror = event => { if (['canceled','interrupted'].includes(event.error)) return; toast('Η φωνή δεν είναι διαθέσιμη τώρα. Μπορείς να συνεχίσεις με το γραπτό παράδειγμα.'); };
  window.speechSynthesis.speak(utterance);
}
function startMic(button,{inputId='answer',consentId='mic-consent',lang='en-US'}={}) {
  if (recognition) { stopVoice(); button.classList.remove('listening'); return; }
  if (!document.getElementById(consentId)?.checked) { toast('Για φωνητική απάντηση, διάβασε και επίλεξε πρώτα την προαιρετική συγκατάθεση.'); document.getElementById(consentId)?.focus(); return; }
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) { toast('Η φωνητική εισαγωγή δεν υποστηρίζεται. Μπορείς να γράψεις την απάντησή σου.'); return; }
  if (!window.isSecureContext) { toast('Το μικρόφωνο χρειάζεται ασφαλή σύνδεση HTTPS ή localhost. Χρησιμοποίησε πληκτρολόγηση.'); return; }
  recognition = new Recognition();
  recognition.lang = lang; recognition.interimResults = false; recognition.continuous = false;
  button.dataset.micIdleLabel=button.getAttribute('aria-label') || 'Ενεργοποίηση μικροφώνου';
  button.classList.add('listening'); button.setAttribute('aria-label','Διακοπή ακρόασης');
  toast('Το μικρόφωνο είναι ενεργό. Μίλησε όταν εμφανιστεί η άδεια του browser.');
  const voiceInput=document.getElementById(inputId);
  recognition.onresult = event => { if (voiceInput?.isConnected) { voiceInput.value=event.results[0][0].transcript; voiceInput.dispatchEvent(new Event('input',{bubbles:true})); voiceInput.focus(); toast('Η φωνή έγινε κείμενο. Έλεγξέ το πριν το στείλεις.'); } };
  recognition.onerror = () => toast('Δεν μπόρεσα να χρησιμοποιήσω το μικρόφωνο. Έλεγξε την άδεια ή γράψε την απάντησή σου.');
  recognition.onend = () => { recognition = null; button.classList.remove('listening'); button.setAttribute('aria-label',button.dataset.micIdleLabel || 'Ενεργοποίηση μικροφώνου'); };
  try { recognition.start(); } catch { recognition = null; button.classList.remove('listening'); toast('Η φωνητική εισαγωγή δεν ξεκίνησε. Μπορείς να πληκτρολογήσεις.'); }
}
function normalize(answer) { return String(answer).normalize('NFKC').toLowerCase().trim().replace(/[.!?,;:]+$/g,'').trim().replace(/\s+/g,' '); }
function formError(error, id = 'form-error') { const target = document.getElementById(id); if (target) { target.textContent = error.message; target.hidden = false; target.scrollIntoView({block:'nearest'}); } else toast(error.message); }
async function withBusy(form, callback) {
  if (busy) return;
  busy = true;
  const button = form.querySelector('button[type="submit"],button[data-action="review-retry"]');
  if (button) { button.disabled = true; button.setAttribute('aria-busy','true'); }
  try { await callback(); } catch(error) { if(form.isConnected) formError(error,form.dataset.errorTarget || (form.id === 'delete-form' ? 'delete-error' : 'form-error')); }
  finally { busy = false; if (button?.isConnected) { button.disabled = false; button.removeAttribute('aria-busy'); } }
}
app.addEventListener('submit', event => {
  event.preventDefault();
  const form = event.target;
  const data = Object.fromEntries(new FormData(form));
  if (form.id==='teacher-form') { sendTeacherMessage().catch(error=>formError(error,'teacher-error')); return; }
  withBusy(form, async () => {
    if(['reset-request-form','reset-password-form','verify-email-form','verify-email-request-form','change-email-form','confirm-email-change-form'].includes(form.id)) {
      await submitMailForm(form,data);
    } else if(form.id==='assessment-form') {
      await submitAssessment();
    } else if(form.id==='support-create-form' || form.id==='support-reply-form') {
      await saveSupport(form,data);
    } else if(form.id==='notification-preferences-form') {
      const view=state.notifications;
      const prefs=await api('notifications/preferences',{method:'PUT',body:Object.fromEntries(['reminders','achievements','supportReplies'].map(key=>[key,data[key]==='on']))});
      if(!featureMatches('notifications',view)) return;
      view.preferences=prefs.preferences; view.error=null; render(); toast('Οι προτιμήσεις ειδοποιήσεων αποθηκεύτηκαν.');
    } else if(form.id==='admin-lesson-form') {
      await saveAdminDraft();
    } else if(form.id==='admin-support-reply-form') {
      await replyAdminSupport(data);
    } else if (form.id === 'account-profile-form') {
      const userId=state.session.user.id,epoch=authEpoch;
      const session=await api('account/profile',{method:'PUT',body:{name:data.name}});
      if (!accountMatches(userId,epoch)) return;
      state.session=session; render(); toast('Το όνομά σου ενημερώθηκε.');
    } else if (form.id === 'password-form') {
      if (data.newPassword !== data.confirmPassword) throw new Error('Οι δύο νέοι κωδικοί δεν ταιριάζουν.');
      const userId=state.session.user.id,epoch=authEpoch;
      const session=await api('account/password',{method:'POST',body:{currentPassword:data.currentPassword,newPassword:data.newPassword}});
      if (!accountMatches(userId,epoch)) return;
      state.session=session; authEpoch++; resetPrivateViews(); render(); toast('Ο κωδικός άλλαξε. Οι άλλες συνδέσεις αποσυνδέθηκαν.');
    } else if (form.id === 'auth-form') {
      await api(form.dataset.mode, {method:'POST',body:{...data,ageConfirmed:data.ageConfirmed === 'on'}});
      await refresh(); authEpoch++; resetPrivateViews(); go(state.session.profile ? 'dashboard' : 'onboarding');
    } else if (form.id === 'onboarding-form') {
      await api('profile',{method:'PUT',body:{...data,goalMinutes:Number(data.goalMinutes),ageGroup:'adult',level:'A1'}});
      await refresh(); go('dashboard');
    } else if (form.id === 'settings-form') {
      await api('profile',{method:'PUT',body:{...state.session.profile,goalMinutes:Number(data.goalMinutes)}});
      await refresh(); render(); toast('Ο ημερήσιος στόχος σου αποθηκεύτηκε.');
    } else if (form.id === 'delete-form') {
      await api('account',{method:'DELETE',body:{password:data.password}}); await refresh(); authEpoch++; resetPrivateViews(); go('home'); toast('Ο λογαριασμός και η πρόοδός σου διαγράφηκαν από την ενεργή βάση.');
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
            dashboardReview = null; state.studyPlan=null;
            if (state.session.user?.id !== userId) return;
            const stillInLesson = learningContext(id,visit,userId);
            state.session = session; state.library=null;
            loadNotificationSummary(true);
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
  const exerciseButton=event.target.closest('[data-exercise-choice],[data-exercise-token],[data-exercise-clear],[data-exercise-listen]');
  if (exerciseButton && !exerciseButton.disabled) {
    if (exerciseButton.hasAttribute('data-exercise-listen')) { speak(exerciseButton.dataset.exerciseListen); return; }
    const form=exerciseButton.closest('form'),input=form?.querySelector('input[name=answer]');
    if (!input || input.readOnly) return;
    if (exerciseButton.hasAttribute('data-exercise-choice')) {
      input.value=exerciseButton.dataset.exerciseChoice;
      form.querySelectorAll('[data-exercise-choice]').forEach(button=>button.setAttribute('aria-pressed',String(button===exerciseButton)));
    } else if (exerciseButton.hasAttribute('data-exercise-token')) { input.value=[input.value.trim(),exerciseButton.dataset.exerciseToken].filter(Boolean).join(' '); exerciseButton.disabled=true; }
    else { input.value=''; form.querySelectorAll('[data-exercise-token]').forEach(button=>button.disabled=false); }
    input.dispatchEvent(new Event('input',{bubbles:true})); input.focus(); return;
  }
  const speakButton = event.target.closest('[data-speak]');
  if (speakButton) { speak(speakButton.dataset.speak); return; }
  const button = event.target.closest('[data-action]');
  if (!button || button.disabled) return;
  const action = button.dataset.action;
  if(action==='email-status-retry') { if(!busy) loadEmailStatus(true); }
  else if(action==='library-reload') { beginLibraryLoad(); render(); }
  else if(action==='library-bookmark') await saveLibraryBookmark(button);
  else if(['library-all','library-saved','library-clear'].includes(action)) {
    const view=state.library; if(!view || !featureMatches('library',view)) return;
    view.bookmarkedOnly=action==='library-saved'; if(action==='library-clear') view.query='';
    render(); document.querySelector(action==='library-clear' ? '#library-search' : `[data-action="${action}"]`)?.focus({preventScroll:true});
  }
  else if(action==='study-plan-retry') { beginStudyPlanLoad(); render(); }
  else if(action==='assessment-catalog') { if(!busy) { beginAssessmentLoad(); render(); } }
  else if(action==='assessment-start') await startAssessment(button.dataset.kind,button.dataset.unitId,button);
  else if(action==='assessment-back') {
    const view=state.assessment;
    if(busy || view.pending || view.index===0) return;
    view.answers[view.index]=document.querySelector('#assessment-answer')?.value || ''; view.index--; view.error=null; render();
  }
  else if(action==='assessment-submit-retry') await featureAction(button,submitAssessment,'assessment-error');
  else if(action==='assessment-last-result') {
    const view=state.assessment,catalog=view.catalog;
    const result=button.dataset.kind==='placement' ? catalog.diagnostic.lastResult : catalog.units.find(unit=>unit.id===button.dataset.unitId)?.lastResult;
    if(result && !busy) { view.result=result; render(); }
  }
  else if(action==='support-refresh') { if(!busy) { beginSupportLoad(); render(); } }
  else if(action==='support-status') await featureAction(button,()=>changeSupportStatus(button.dataset.status),'support-error');
  else if(action==='notification-refresh') { if(!busy) { beginNotificationsLoad(); render(); } }
  else if(action==='notification-read-all' || action==='notification-read') await featureAction(button,()=>readNotifications(button.dataset.notificationId),'notification-error');
  else if(action==='admin-reload') { if(!busy) { beginAdminLoad(); render(); } }
  else if(action==='admin-tab') {
    if(busy) return;
    const view=state.admin; view.tab=button.dataset.tab; view.error=null;
    if(view.tab==='support') loadAdminSupport(view);
    render();
  }
  else if(action==='admin-select') {
    if(busy) return;
    const record=state.admin.lessons.find(item=>item.id===button.dataset.lessonId);
    if(record) { selectAdminLesson(state.admin,record); render(); }
  }
  else if(action==='admin-copy') {
    if(busy) return;
    const view=state.admin,record=view.lessons.find(item=>item.id===view.selectedId);
    if(record) { selectAdminLesson(view,record,{copy:true}); render(); toast('Άλλαξε το πεδίο id του αντιγράφου σε ένα νέο, μοναδικό αναγνωριστικό.'); }
  }
  else if(action==='admin-new') {
    if(busy) return;
    const view=state.admin;
    const phrases=[['Hello','Γεια σου'],['Goodbye','Αντίο'],['Thank you','Ευχαριστώ']];
    const lesson={id:'new-lesson',title:'Μικρές καθημερινές φράσεις',subtitle:'Χαιρετισμοί και ευγένεια',durationMinutes:5,xp:30,objective:'Ο μαθητής εξασκείται σε τρεις μικρές καθημερινές φράσεις.',cards:phrases.map(([word,translation])=>({word,translation})),exercises:phrases.map(([answer,translation],index)=>({id:`phrase-${index+1}`,type:'translate',prompt:`Πώς λέμε «${translation}»;`,answer,acceptedAnswers:[answer],explanation:`${answer} σημαίνει ${translation.toLowerCase()}.`}))};
    Object.assign(view,{creating:true,selectedId:null,editorJson:JSON.stringify(lesson,null,2),editorDirty:false,preview:null,error:null}); render();
  }
  else if(['admin-add-card','admin-add-exercise','admin-remove-card','admin-remove-exercise'].includes(action)) changeAdminRows(action,Number(button.dataset.rowIndex));
  else if(action==='admin-sync-editor') {
    if(busy) return;
    try { parseAdminEditor(); state.admin.advancedOpen=true; state.admin.error=null; render(); } catch(error) { formError(error,'admin-error'); }
  }
  else if(action==='admin-preview') {
    if(busy) return;
    try { state.admin.preview=parseAdminEditor(); state.admin.error=null; render(); document.querySelector('#admin-preview')?.scrollIntoView({block:'start'}); }
    catch(error) { formError(error,'admin-error'); }
  }
  else if(action==='admin-publish') await featureAction(button,publishAdminDraft,'admin-error');
  else if(action==='admin-support-open') { loadAdminSupport(state.admin,button.dataset.ticketId); render(); }
  else if(action==='admin-support-back') { loadAdminSupport(); render(); }
  else if(action==='admin-support-prev' || action==='admin-support-next') {
    const view=state.admin; view.supportOffset=Math.max(0,view.supportOffset+(action==='admin-support-prev' ? -20 : 20)); loadAdminSupport(view); render();
  }
  else if (action === 'teacher-retry-status' || action === 'teacher-clear') { stopVoice(); beginTeacherStatus(action==='teacher-retry-status'); render(); }
  else if (action === 'teacher-stop') stopTeacherWork();
  else if (action === 'teacher-mic') startMic(button,{inputId:'teacher-message',consentId:'teacher-mic-consent',lang:'el-GR'});
  else if (action === 'teacher-speak') { const message=state.teacher?.messages[Number(button.dataset.messageIndex)]; if (message?.role==='assistant') speak(message.content,'el-GR'); }
  else if (action === 'teacher-board-speak') { const card=state.teacher?.board?.card; if (card) speak(card.example || card.word); }
  else if (action === 'reload-sessions') { accountSessions=null; loadAccountSessions(); const target=document.querySelector('#account-session-list'); if (target) target.innerHTML=accountSessionList(); }
  else if (action === 'revoke-session') await revokeAccountSession(button);
  else if (action === 'revoke-other-sessions') await revokeAccountSession(button,true);
  else if (action === 'speak-demo') speak('car. This is a car.');
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
  else if (action === 'review-start-mistakes') { if(!busy) setReviewMode('mistakes'); }
  else if (action === 'review-mistakes-reload') { refreshPracticeInsights(state.review,{refreshQueue:true}); render(); }
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
    if (busy) { toast('Περίμενε να ολοκληρωθεί η τρέχουσα αποθήκευση και δοκίμασε ξανά.'); return; }
    stopTeacherWork(); button.disabled = true;
    try { await api('logout',{method:'POST',body:{}}); await refresh(); authEpoch++; resetPrivateViews(); go('home'); } catch(error) { toast(error.message); button.disabled=false; }
  } else if (action === 'export') {
    button.disabled = true;
    try { const data = await api('export'); const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})); const link = document.createElement('a'); link.href=url; link.download='academy-my-data.json'; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000); toast('Το αντίγραφο δεδομένων σου είναι έτοιμο.'); } catch(error) { toast(error.message); } finally { button.disabled=false; }
  } else if (action === 'install' && installPrompt) { await installPrompt.prompt(); installPrompt=null; }
});
app.addEventListener('input',event=>{
  if(event.target.id==='library-search' && state.library && featureMatches('library',state.library)) { state.library.query=event.target.value; updateLibraryResults(); }
  if(event.target.hasAttribute('data-lesson-field') && state.admin && event.target.tagName!=='SELECT') changeAdminField(event.target);
  if(event.target.id==='assessment-answer' && state.assessment && !state.assessment.pending) state.assessment.answers[state.assessment.index]=event.target.value;
  if(event.target.id==='admin-lesson-json' && state.admin) {
    state.admin.editorJson=event.target.value; state.admin.editorDirty=true; state.admin.preview=null; state.admin.advancedOpen=true;
    const publish=document.querySelector('[data-action="admin-publish"]'); if(publish) publish.disabled=true;
    const preview=document.querySelector('#admin-preview'); if(preview) preview.remove();
  }
  if (event.target.id==='teacher-message' && state.teacher) state.teacher.draft=event.target.value;
  if (event.target.id==='review-answer' && state.review && !state.review.pending) state.review.answer=event.target.value;
});
app.addEventListener('change', event => {
  if(event.target.hasAttribute('data-lesson-field') && state.admin && event.target.tagName==='SELECT') changeAdminField(event.target);
  if(event.target.id==='admin-support-status' && state.admin) { state.admin.supportFilter=event.target.value; state.admin.supportOffset=0; loadAdminSupport(); render(); }
  if (event.target.id==='teacher-lesson' && state.teacher && !state.teacher.pending) { stopVoice(); Object.assign(state.teacher,{lessonId:event.target.value,messages:[],draft:'',board:null,error:null}); render(); }
  if (event.target.id==='teacher-consent' && state.teacher) { state.teacher.consent=event.target.checked; const submit=document.querySelector('#teacher-form button[type=submit]'); if (submit) submit.disabled=!state.teacher.consent || !state.teacher.status?.available || state.teacher.pending; }
  if (event.target.id==='teacher-mic-consent' && state.teacher) { state.teacher.micConsent=event.target.checked; if (!state.teacher.micConsent) stopVoice(); }
  if (event.target.id === 'theme-select') { document.documentElement.dataset.theme=event.target.value; try { localStorage.setItem('academy-theme',event.target.value); } catch {} }
  if (event.target.id === 'mic-consent') { state.micConsent=event.target.checked; if (!state.micConsent) stopVoice(); }
});
document.querySelector('.skip-link').addEventListener('click', event => {
  event.preventDefault();
  const main = document.querySelector('#main-content');
  if (main) { main.setAttribute('tabindex','-1'); main.focus(); main.scrollIntoView({block:'start'}); }
});
window.addEventListener('hashchange',render);
window.addEventListener('pagehide',()=>{ stopVoice(); if (teacherController) teacherController.abort(); });
window.addEventListener('offline',()=>toast('Η σύνδεση χάθηκε. Η ολοκλήρωση μαθήματος χρειάζεται διαθέσιμο διακομιστή.'));
window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); installPrompt=event; });
(async () => {
  try { const [session,curriculum] = await Promise.all([api('session'),api('curriculum')]); state.session=session; state.curriculum=curriculum; render(); }
  catch(error) { app.innerHTML=`<main id="main-content" class="loading-screen"><img src="/assets/logo.svg" width="54" height="54" alt=""><h1>Χρειαζόμαστε μια μικρή επανασύνδεση.</h1><p>${escape(error.message)}</p><p>Βεβαιώσου ότι ο διακομιστής λειτουργεί και ανανέωσε τη σελίδα.</p><a class="button button-primary" href="/">Δοκίμασε ξανά</a></main>`; }
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
})();
