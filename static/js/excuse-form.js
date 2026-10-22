// The excuse request form adapts to the chosen reason: a late arrival is one day
// with a time, "other" needs a note. The server checks the same rules regardless.
(function () {
  var form = document.querySelector('[data-excuse-form]');
  if (!form) return;
  var date = form.querySelector('[data-excuse-date]');
  var until = form.querySelector('input[name="until"]');
  var several = form.querySelector('[data-several]');
  var untilLabel = form.querySelector('[data-until]');
  var lateOnly = form.querySelector('[data-only="late"]');
  var severalBlock = form.querySelector('[data-only="several"]');
  var note = form.querySelector('input[name="note"]');
  var noteHint = form.querySelector('[data-note-hint]');

  function kind() {
    var checked = form.querySelector('input[name="kind"]:checked');
    return checked ? checked.value : '';
  }

  function update() {
    var late = kind() === 'late';
    lateOnly.hidden = !late;
    severalBlock.hidden = late;
    if (late) several.checked = false;
    untilLabel.hidden = !several.checked;
    until.required = several.checked;
    if (!several.checked) until.value = '';
    until.min = date.value;
    var other = kind() === 'other';
    note.required = other;
    noteHint.textContent = other ? '(majburiy)' : '(ixtiyoriy)';
  }

  form.addEventListener('change', update);
  form.querySelectorAll('[data-set-date]').forEach(function (button) {
    button.addEventListener('click', function () {
      date.value = button.getAttribute('data-set-date');
      update();
    });
  });
  update();
})();
