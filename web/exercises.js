// Public exercise presentation only. The server remains authoritative for grading.
const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[character]));

export function renderExerciseExtras(exercise) {
  if (exercise.type === 'reading' && typeof exercise.passage === 'string') {
    return `<section class="exercise-stimulus reading-stimulus" aria-label="Κείμενο ανάγνωσης"><span class="eyebrow">ΔΙΑΒΑΣΕ ΤΟ ΜΙΚΡΟ ΚΕΙΜΕΝΟ</span><p lang="en">${escape(exercise.passage)}</p></section>`;
  }
  if (exercise.type === 'listening' && typeof exercise.audioText === 'string') {
    return `<section class="exercise-stimulus listening-stimulus" aria-label="Άσκηση ακρόασης"><button type="button" class="button button-outline" data-exercise-listen="${escape(exercise.audioText)}">Άκου το κείμενο</button><p class="small-note">Η ακρόαση χρησιμοποιεί τη φωνή του browser. Αν ο ήχος δεν είναι διαθέσιμος, άνοιξε το κείμενο και συνέχισε ως άσκηση ανάγνωσης.</p><details><summary>Δες το κείμενο αν δεν υπάρχει ήχος</summary><p lang="en">${escape(exercise.audioText)}</p></details></section>`;
  }
  return '';
}

export function renderExerciseInput(exercise, {
  answer = '', readonly = false, inputId = 'answer', inputName = 'answer'
} = {}) {
  let controls = '';
  if (exercise.type === 'multiple-choice' && Array.isArray(exercise.choices)) {
    controls = `<div class="exercise-choice-grid" role="group" aria-label="Επίλεξε μια απάντηση">${exercise.choices.map(choice => `<button type="button" class="button button-outline exercise-choice" data-exercise-choice="${escape(choice)}" aria-pressed="${String(answer).toLowerCase() === String(choice).toLowerCase()}" ${readonly ? 'disabled' : ''}><span lang="en">${escape(choice)}</span></button>`).join('')}</div><p class="small-note">Διάλεξε μια επιλογή ή γράψε την απάντηση. Μετά πάτησε «Έλεγχος απάντησης».</p>`;
  } else if (exercise.type === 'word-order' && Array.isArray(exercise.tokens)) {
    controls = `<div class="exercise-token-bank" role="group" aria-label="Λέξεις για τη σειρά της πρότασης">${exercise.tokens.map((token, index) => `<button type="button" class="button button-outline exercise-token" data-exercise-token="${escape(token)}" data-exercise-token-index="${index}" ${readonly ? 'disabled' : ''}><span lang="en">${escape(token)}</span></button>`).join('')}</div><button type="button" class="text-button exercise-clear" data-exercise-clear ${readonly ? 'disabled' : ''}>Καθάρισε την πρόταση</button><p class="small-note">Πάτησε τις λέξεις στη σωστή σειρά ή πληκτρολόγησε ολόκληρη την πρόταση. Χρησιμοποίησε όλες τις λέξεις.</p>`;
  }
  return `${controls}<label class="answer-label" for="${escape(inputId)}">Η απάντησή σου στα αγγλικά</label><div class="answer-input-row"><input id="${escape(inputId)}" name="${escape(inputName)}" lang="en" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="160" required ${readonly ? 'readonly' : ''} value="${escape(answer)}" placeholder="Γράψε εδώ…"></div>`;
}
