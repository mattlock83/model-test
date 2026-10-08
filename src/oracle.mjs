const headings = {
  v_Home: 'Make something by hand',
  v_Form: 'Book your workshop',
  v_Error: 'Book your workshop',
  v_Review: 'Review your booking',
  v_Confirmed: 'You’re on the list',
};

export function normalizedBooking(input) {
  return Object.fromEntries(['name', 'email', 'seats'].map((key) => [key, String(input[key] ?? '').trim()]));
}

// Requirements oracle, independent of the application code and its validation result.
export function inputErrors(input) {
  const { name, email, seats } = normalizedBooking(input);
  const errors = [];
  if (name.length < 2 || name.length > 60) errors.push('Full name must be between 2 and 60 characters.');
  const parts = email.split('@');
  const domainParts = (parts[1] ?? '').split('.');
  if (parts.length !== 2 || !parts[0] || /\s/.test(email) || domainParts.length < 2 || domainParts.some((part) => !part)) {
    errors.push('Enter a valid email address, such as alex@example.com.');
  }
  if (!seats || [...seats].some((character) => character < '0' || character > '9') || !Number.isInteger(Number(seats)) || Number(seats) < 1 || Number(seats) > 4) {
    errors.push('Seats must be a whole number from 1 to 4.');
  }
  return errors;
}

export async function observe(browser) {
  const snapshot = await browser.snapshot();
  const { text } = await browser.call('browser_evaluate', { function: `() => {
    const visible = (element) => Boolean(element && element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden');
    const content = (element) => visible(element) ? element.innerText.replace(/\\s+/g, ' ').trim() : null;
    const find = (selector) => [...document.querySelectorAll(selector)].find(visible);
    const value = (selector) => { const element = find(selector); return element ? element.value : null; };
    const confirmation = find('[role="status"]');
    const confirmationValues = confirmation ? [...confirmation.querySelectorAll('strong')].map(content) : [];
    const buttons = [...document.querySelectorAll('button')].filter(visible).map((button) => {
      const copy = button.cloneNode(true);
      copy.querySelectorAll('[aria-hidden="true"]').forEach((element) => element.remove());
      return (button.getAttribute('aria-label') || copy.textContent).replace(/\\s+/g, ' ').trim();
    });
    return {
      heading: content(find('h1')),
      text: document.body.innerText,
      buttons,
      alerts: [...document.querySelectorAll('[role="alert"]')].filter(visible).map(content),
      fields: { name: value('#full-name'), email: value('#email-address'), seats: value('#seats') },
      summary: {
        name: content(find('#review-name')) ?? confirmationValues[0] ?? null,
        email: content(find('#review-email')) ?? confirmationValues[2] ?? null,
        seats: content(find('#review-seats')) ?? confirmationValues[1]?.split(' ')[0] ?? null,
        total: content(find('#review-total'))
      }
    };
  }` });
  const result = text.match(/### Result\s*\n([\s\S]*?)(?=\n### |$)/)?.[1];
  if (!result) throw new Error('Playwright MCP evaluate did not return browser observation JSON.');
  let evidence;
  try { evidence = JSON.parse(result.trim()); }
  catch (cause) { throw new Error('Could not parse Playwright MCP browser observation JSON.', { cause }); }
  if (!evidence || typeof evidence !== 'object' || !Array.isArray(evidence.buttons) || !Array.isArray(evidence.alerts)) {
    throw new Error('Playwright MCP browser observation is incomplete.');
  }
  return { ...evidence, snapshot };
}

export function identifyState(evidence) {
  const has = (button) => evidence.buttons?.includes(button);
  if (evidence.heading === headings.v_Home && has('Book a place')) return 'v_Home';
  if (evidence.heading === headings.v_Form && has('Review booking')) return evidence.alerts?.length ? 'v_Error' : 'v_Form';
  if (evidence.heading === headings.v_Review && has('Confirm booking')) return 'v_Review';
  if (evidence.heading === headings.v_Confirmed && has('Start again')) return 'v_Confirmed';
  return '__unknown__';
}

export function checkState(expectedState, evidence, { booking, previousEdge } = {}) {
  const observedState = identifyState(evidence);
  const checks = [];
  const check = (description, passed) => checks.push({ description, passed: Boolean(passed) });
  const hasButton = (name) => check(`Visible button: ${name}`, evidence.buttons?.includes(name));
  check(`State is ${expectedState}`, observedState === expectedState);
  check('Expected page heading is visible', Boolean(headings[expectedState]) && evidence.heading === headings[expectedState]);
  if (expectedState === 'v_Home') hasButton('Book a place');
  if (expectedState === 'v_Form' || expectedState === 'v_Error') {
    hasButton('Review booking');
    hasButton('Back to home');
    check('All three form fields are visible', ['name', 'email', 'seats'].every((key) => typeof evidence.fields?.[key] === 'string'));
    if (expectedState === 'v_Form') check('No validation alert', evidence.alerts?.length === 0);
    if (expectedState === 'v_Error') {
      const alerts = (evidence.alerts ?? []).join('\n');
      check('Validation alert explains the problem', alerts.includes('Check your details'));
      if (booking) {
        const errors = inputErrors(booking);
        check('Submitted input violates at least one requirement', errors.length > 0);
        for (const error of errors) check(error, alerts.includes(error));
      }
    }
    const edgeName = typeof previousEdge === 'string' ? previousEdge : previousEdge?.name ?? '';
    if (booking && /edit/i.test(edgeName)) {
      for (const [key, value] of Object.entries(normalizedBooking(booking))) {
        check(`Editing preserves ${key}`, evidence.fields?.[key] === value);
      }
    }
  }
  if (expectedState === 'v_Review' || expectedState === 'v_Confirmed') {
    check('No validation alert', evidence.alerts?.length === 0);
    if (expectedState === 'v_Review') { hasButton('Edit details'); hasButton('Confirm booking'); }
    else hasButton('Start again');
    if (booking) {
      const values = normalizedBooking(booking);
      for (const [key, value] of Object.entries(values)) {
        check(`Summary displays submitted ${key}`, evidence.summary?.[key] === value);
      }
      if (expectedState === 'v_Review') check('Total is $45 per seat', evidence.summary?.total === `$${Number(values.seats) * 45}`);
    }
  }
  return { mode: 'offline', observedState, passed: checks.every((item) => item.passed), checks };
}
