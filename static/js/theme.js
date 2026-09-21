// Dark is the default; a saved choice wins, so the toggle keeps working.
try { document.documentElement.dataset.theme = localStorage.getItem('sic-theme') || 'dark'; } catch (_) { document.documentElement.dataset.theme = 'dark'; }
