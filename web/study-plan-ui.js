// Display only: the server derives all private counts and prerequisites.
const dayLabel = value => {
  const date = new Date(`${value}T12:00:00Z`);
  return Number.isFinite(date.getTime())
    ? new Intl.DateTimeFormat('el-GR', {day:'numeric', month:'short', timeZone:'UTC'}).format(date)
    : value;
};

function loadState(view, {escape:e}, compact=false) {
  if (!view || view.loading) return `<section class="${compact ? 'continue-card' : 'side-card'} study-loading"><h2>Το δικό σου επόμενο βήμα.</h2><p role="status">Φορτώνουμε το πλάνο και την πρόοδό σου…</p></section>`;
  if (view.error) return `<section class="${compact ? 'continue-card' : 'side-card'} study-loading"><h2>Το πλάνο δεν φορτώθηκε ακόμη.</h2><p class="${compact ? '' : 'form-error'}" role="alert">${e(view.error)}</p><button type="button" class="button ${compact ? 'button-cream' : 'button-outline'}" data-action="study-plan-retry">Νέα προσπάθεια</button></section>`;
  return '';
}

export function renderStudyRecommendation(view,h) {
  const pending=loadState(view,h,true);
  if(pending) return pending;
  const {escape:e,icon}=h, plan=view.data, item=plan.recommendation;
  const isLesson=item.kind==='lesson' && plan.nextLesson;
  const href=isLesson ? `#lesson/${encodeURIComponent(plan.nextLesson.id)}` : '#review';
  const label=isLesson ? (plan.nextLesson.resume ? 'Συνέχισε από εκεί που έμεινες' : 'Στο επόμενο μάθημα') : item.kind==='review' ? 'Στις επαναλήψεις μου' : 'Εξάσκηση όσων έμαθες';
  return `<section id="study-recommendation" class="continue-card" data-recommendation="${e(item.kind)}"><span class="eyebrow">ΤΟ ΔΙΚΟ ΣΟΥ ΕΠΟΜΕΝΟ ΒΗΜΑ</span><h2>${e(item.title)}</h2><p>${e(item.description)}</p>${isLesson ? `<p class="study-resume-note">${plan.nextLesson.resume ? 'Η αποθηκευμένη θέση σου σε περιμένει. ' : ''}Εκτιμώμενη διάρκεια ολόκληρου μαθήματος: ${e(plan.nextLesson.durationMinutes)} λεπτά.</p>` : ''}<a class="button button-cream" href="${href}">${e(label)} ${icon('arrow')}</a><span class="continue-decor" aria-hidden="true">${item.kind==='review' ? '↻' : 'Aa'}</span></section>`;
}

export function renderStudyProgress(view,h) {
  const pending=loadState(view,h);
  if(pending) return pending;
  const {escape:e,icon}=h, plan=view.data, session=plan.suggestedSession;
  const activeDays=plan.dailyActivity.filter(day=>day.completedLessons>0).length;
  const maximumDailyCompletions=Math.max(1,...plan.dailyActivity.map(day=>day.completedLessons));
  return `<div id="study-plan-details" class="study-plan-details">
    <section class="side-card study-session"><div class="section-heading"><div><span class="eyebrow">ΕΝΑ ΜΙΚΡΟ ΠΛΑΝΟ, ΜΕ ΤΟΝ ΡΥΘΜΟ ΣΟΥ</span><h2>Η επόμενη εξάσκησή σου.</h2></div>${icon('clock')}</div><p id="study-plan-summary">${e(plan.summary.completedLessons)} από ${e(plan.summary.totalLessons)} μαθήματα · ${e(plan.summary.completionPercent)}% της τρέχουσας διαδρομής.</p><p>Προσωπικός στόχος: <strong>${e(session.goalMinutes)} λεπτά</strong>. ${plan.summary.dueReviews ? `<a href="#review">${e(plan.summary.dueReviews)} στοιχεία έχουν φτάσει στην ώρα επανάληψής τους.</a>` : 'Δεν υπάρχουν προγραμματισμένα στοιχεία για επανάληψη αυτή τη στιγμή.'}</p>
    ${session.lessons.length ? `<ol class="study-session-list">${session.lessons.map(lesson=>`<li><div>${plan.nextLesson?.id===lesson.id ? `<a href="#lesson/${encodeURIComponent(lesson.id)}">${e(lesson.title)}</a>` : `<span>${e(lesson.title)}</span>`}${lesson.resume ? '<small>Συνέχεια από αποθηκευμένη θέση</small>' : plan.nextLesson?.id!==lesson.id ? '<small>Στη συνέχεια, αφού ολοκληρωθούν τα προηγούμενα</small>' : ''}</div><span class="quiet">περ. ${e(lesson.durationMinutes)}′</span></li>`).join('')}</ol><p class="small-note">Εκτίμηση ολόκληρων μαθημάτων στο πλάνο: ${e(session.estimatedLessonMinutes)} λεπτά. Δεν μετράμε πραγματικό χρόνο μελέτης· μια επανάληψη ή η συνέχεια μαθήματος μπορεί να χρειαστεί διαφορετικό χρόνο.</p>` : '<p class="small-note">Το εισαγωγικό υλικό ολοκληρώθηκε. Μπορείς να επαναλάβεις όσα έμαθες χωρίς επιπλέον XP.</p>'}<p class="small-note">${e(session.note)}</p></section>
    <section class="side-card study-activity"><div class="section-heading"><div><span class="eyebrow">ΑΠΟ ΤΙΣ ΠΡΑΓΜΑΤΙΚΕΣ ΟΛΟΚΛΗΡΩΣΕΙΣ ΣΟΥ</span><h2>Οι τελευταίες 14 ημέρες.</h2></div></div><p class="quiet">${activeDays} ${activeDays===1 ? 'ημέρα με νέα ολοκλήρωση' : 'ημέρες με νέες ολοκληρώσεις'}. Οι ημέρες υπολογίζονται σε ώρα Αθήνας.</p><div class="study-activity-chart" aria-hidden="true">${plan.dailyActivity.map(day=>`<div class="study-activity-day"><span class="study-activity-bar activity-level-${Math.round(day.completedLessons/maximumDailyCompletions*10)}"></span><small>${e(dayLabel(day.date))}</small></div>`).join('')}</div><details class="study-activity-records"><summary>Αναλυτικός πίνακας ημερών</summary><table id="study-activity-table"><caption>Νέα ολοκληρωμένα μαθήματα και XP ανά ημέρα, ώρα Αθήνας</caption><thead><tr><th scope="col">Ημέρα</th><th scope="col">Μαθήματα</th><th scope="col">XP</th></tr></thead><tbody>${plan.dailyActivity.map(day=>`<tr data-activity-date="${e(day.date)}"><th scope="row"><time datetime="${e(day.date)}">${e(dayLabel(day.date))}</time>${day.date===plan.today ? ' · Σήμερα' : ''}</th><td>${e(day.completedLessons)}</td><td>${e(day.xp)}</td></tr>`).join('')}</tbody></table></details><p class="small-note">Η επανάληψη δεν προσθέτει XP ή νέα ολοκλήρωση. Οι μηδενικές ημέρες δεν σημαίνουν ότι δεν διάβασες: εδώ καταγράφονται μόνο νέες ολοκληρώσεις μαθημάτων.</p></section>
    <section class="study-unit-section"><div class="section-heading"><div><span class="eyebrow">ΒΗΜΑ ΒΗΜΑ ΣΤΗ ΔΙΑΔΡΟΜΗ</span><h2>Οι ενότητές σου.</h2></div></div><div class="study-unit-grid">${plan.units.map(unit=>`<article class="side-card study-unit" data-study-unit="${e(unit.id)}"><div class="study-unit-title"><h3>${e(unit.title)}</h3><span class="count-pill">${e(unit.completedLessons)} / ${e(unit.totalLessons)}</span></div><progress max="${e(unit.totalLessons)}" value="${e(unit.completedLessons)}" aria-label="${e(unit.title)}: ${e(unit.completedLessons)} από ${e(unit.totalLessons)} μαθήματα">${e(unit.completedLessons)} από ${e(unit.totalLessons)}</progress><p class="small-note">${unit.status==='completed' ? 'Ολοκληρώθηκε' : unit.status==='available' ? 'Έτοιμη για το επόμενο βήμα σου' : 'Ανοίγει μετά τα προηγούμενα μαθήματα'}</p><ol>${unit.lessons.map(lesson=>`<li>${lesson.unlocked ? `<a href="#lesson/${encodeURIComponent(lesson.id)}">${e(lesson.title)}</a>` : `<span>${e(lesson.title)}</span>`}<span class="study-unit-state">${lesson.completed ? `${icon('check')} Ολοκληρώθηκε` : lesson.unlocked ? 'Διαθέσιμο' : `${icon('lock')} Κλειδωμένο`}</span></li>`).join('')}</ol></article>`).join('')}</div><p class="small-note">${e(plan.notice)}</p></section>
  </div>`;
}

export function renderLessonMap(lessons,completed,h) {
  const {escape:e,icon}=h,units=[];
  for(let offset=0;offset<lessons.length;offset+=3) units.push({offset,lessons:lessons.slice(offset,offset+3)});
  return units.map(unit=>`<section class="journey-section" data-map-unit="${unit.offset/3+1}"><div class="section-heading"><div><span class="eyebrow">ΕΙΣΑΓΩΓΙΚΟ A1 · ΕΝΟΤΗΤΑ ${String(unit.offset/3+1).padStart(2,'0')}</span><h2>${e(unit.lessons[0].title)}</h2></div><span class="count-pill">${unit.lessons.filter(lesson=>completed.includes(lesson.id)).length} / ${unit.lessons.length}</span></div><div class="lesson-path">${unit.lessons.map((lesson,localIndex)=>{
    const index=unit.offset+localIndex,done=completed.includes(lesson.id),unlocked=index===0 || lessons.slice(0,index).every(previous=>completed.includes(previous.id));
    return `<article class="path-row ${done ? 'completed' : unlocked ? 'current' : 'locked'}"><span class="path-node">${done ? icon('check') : unlocked ? icon('book') : icon('lock')}</span><div class="path-card"><div class="path-meta">ΜΑΘΗΜΑ ${String(index+1).padStart(2,'0')} ${done ? '<span>Ολοκληρώθηκε</span>' : !unlocked ? '<span>Ανοίγει μετά το προηγούμενο</span>' : '<span>Η σειρά σου!</span>'}</div><h3>${e(lesson.title)}</h3><p>${e(lesson.subtitle)}</p><div class="path-bottom"><span>${icon('clock')} περ. ${lesson.durationMinutes} λεπτά <span class="path-xp">· ${lesson.xp} XP</span></span>${unlocked ? `<a class="${done ? 'text-link' : 'button button-small button-primary'}" href="#lesson/${encodeURIComponent(lesson.id)}">${done ? 'Επανάληψη' : 'Ξεκίνα'} ${icon('arrow')}</a>` : '<span class="locked-label">Κλειδωμένο</span>'}</div></div></article>`;
  }).join('')}</div></section>`).join('');
}
