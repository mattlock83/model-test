const app = document.querySelector('#app');
const escapeHtml = value => String(value).replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[c]);
const money = cents => `AUD ${(cents / 100).toFixed(2)}`;
const length = value => Array.from(value).length;
const prices = { 'Ridge walk': 80, 'River paddle': 120 };
let draft = {}, profileDraft = {}, cancelDraft = {}, booking = null, token = '';
let store = null, errors = {}, routeVersion = 0, pickup = 'City visitor centre';

async function api(path, body) {
  const response = await fetch(`/api/trailhead/${path}`, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The demo service is unavailable.');
  return result;
}

function cents(value) {
  // Decimal strings, rounded half up to cents, without binary floating-point drift.
  const [integer, fraction = ''] = String(value).replace(/^\+/, '').split('.');
  const digits = (fraction + '000').slice(0, 3);
  return Number(BigInt(integer || '0') * 100n + BigInt(digits.slice(0, 2)) + (digits[2] >= '5' ? 1n : 0n));
}
const total = data => Number(data['Party size']) * prices[data.Adventure] * 100
  + cents(data['Conservation contribution']);
const link = (state, label, secondary = false) =>
  `<a class="button${secondary ? ' secondary' : ''}" href="#${state}">${label}</a>`;
const actions = (...items) => `<div class="actions">${items.join('')}</div>`;
const rows = data => `<dl>${Object.entries(data).map(([key, value]) =>
  `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value || 'None')}</dd></div>`).join('')}</dl>`;
const field = (name, label, hint, values, type = 'text', multiline = false) => {
  const attributes = `id="${name}" name="${name}" aria-describedby="${name}-hint"
    ${errors[name] ? 'aria-invalid="true"' : ''}`;
  return `<div class="field"><label for="${name}">${label}</label>${multiline
    ? `<textarea ${attributes} rows="3">${escapeHtml(values[name] || '')}</textarea>`
    : `<input ${attributes} type="${type}" value="${escapeHtml(values[name] ?? '')}" autocomplete="off">`}
    <p class="hint" id="${name}-hint">${hint}</p></div>`;
};
const errorSummary = () => Object.keys(errors).length ? `<div class="errors" role="alert">
  <h2>Check your details</h2><ul>${Object.values(errors).map(message => `<li>${escapeHtml(message)}</li>`).join('')}</ul>
  </div>` : '';

function show(state, heading, content, eyebrow = 'TRAILHEAD / YOUR NEXT CHAPTER') {
  document.title = `Trailhead — ${heading}`;
  app.dataset.state = state;
  app.innerHTML = `<p class="eyebrow">${eyebrow}</p><h1>${heading}</h1>${content}`;
  document.querySelectorAll('nav a').forEach(a => {
    if (a.hash === `#${state}`) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  window.scrollTo(0, 0);
}

function collect(form) {
  return Object.fromEntries([...new FormData(form)].map(([key, value]) => [key, value.trim()]));
}
function validateBooking(data) {
  const result = {};
  if (length(data['Traveller name']) < 2 || length(data['Traveller name']) > 60)
    result['Traveller name'] = 'Traveller name must contain 2–60 characters.';
  if (!/^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$/.test(data['Contact email']))
    result['Contact email'] = 'Enter a valid contact email with a dotted domain.';
  if (!/^[+-]?\d+$/.test(data['Party size']) || Number(data['Party size']) < 1 || Number(data['Party size']) > 6)
    result['Party size'] = 'Party size must be a whole number from 1 through 6.';
  if (!Object.hasOwn(prices, data.Adventure)) result.Adventure = 'Adventure must be Ridge walk or River paddle.';
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(data['Conservation contribution'])
      || Number(data['Conservation contribution']) < 0 || Number(data['Conservation contribution']) > 50)
    result['Conservation contribution'] = 'Conservation contribution must be a decimal number from 0 through 50.';
  return result;
}
class PickupPicker extends HTMLElement {
  connectedCallback() {
    if (this.shadowRoot) return;
    const root = this.attachShadow({ mode: 'open' });
    root.innerHTML = `<style>button{font:inherit;padding:12px;border:1px solid #a8b5a6;background:#fffefa;cursor:pointer}
      [role=listbox]{display:grid;gap:4px;margin:8px 0} [hidden]{display:none}</style>
      <slot></slot>
      <div role="listbox" aria-label="Departure pickup options" hidden>
      <button type="button" role="option" data-value="City visitor centre">City visitor centre — 8:00 am</button>
      <button type="button" role="option" data-value="Marina pier">Marina pier — 8:30 am</button></div>`;
    const opener = this.querySelector('[aria-haspopup]'), list = root.querySelector('[role=listbox]');
    opener.onclick = () => { list.hidden = !list.hidden; opener.setAttribute('aria-expanded', String(!list.hidden)); };
    root.querySelectorAll('[role=option]').forEach(option => { option.onclick = () => {
      pickup = option.dataset.value; list.hidden = true; opener.setAttribute('aria-expanded', 'false');
      this.dispatchEvent(new CustomEvent('pickup-change', { bubbles: true, detail: pickup }));
    }; });
  }
}
customElements.define('pickup-picker', PickupPicker);

function bookingForm(state) {
  show(state, state === 'booking_error' ? 'Booking details need attention' : 'Plan your adventure', `
    <div class="layout"><section><p>Tell us who is travelling. Review first; confirm only when you are ready.</p>
    ${errorSummary()}<form id="booking-form" novalidate>
    <div class="field"><p><strong>Departure pickup</strong></p><p id="pickup-summary">Selected pickup: ${escapeHtml(pickup)}</p>
    <pickup-picker aria-label="Departure pickup selector"><button type="button" class="secondary" aria-haspopup="listbox" aria-expanded="false">Change pickup point</button></pickup-picker>
    <p class="hint">Choose City visitor centre or Marina pier. The pickup point can be changed before review.</p></div>
    ${field('Traveller name', 'Lead traveller', '2–60 characters; surrounding spaces are trimmed.', draft)}
    ${field('Contact email', 'Email address', 'A valid address with a dotted domain.', draft, 'email')}
    ${field('Party size', 'Travellers', 'A whole number from 1 through 6.', draft)}
    ${field('Adventure', 'Trip choice', 'Type Ridge walk or River paddle. Other choices are rejected.', draft)}
    ${field('Conservation contribution', 'Conservation gift (AUD)', 'Required decimal amount from 0 through 50. Use 0 for no gift.', draft)}
    <button type="submit">Review adventure</button></form>
    ${actions(link('catalogue', 'Abandon booking', true), ...(state === 'booking_error'
      ? [link('booking_details', 'Clear errors and revise', true)] : []))}</section>
    <aside><h2>Before you commit</h2><p>Ridge walk: AUD 80 per traveller.<br>River paddle: AUD 120 per traveller.</p>
    <p>Your conservation gift is added once to the party total. Equipment and guiding are included.</p>
    <p>Reviewing does not reserve inventory or create a booking.</p>${link('policies', 'Read booking policies', true)}</aside></div>`);
  app.querySelector('pickup-picker').addEventListener('pickup-change', event => {
    const next = event.detail === 'Marina pier' ? 'booking_pickup' : 'booking_details';
    errors = {}; go(next);
  });
  app.querySelector('form').onsubmit = event => {
    event.preventDefault(); draft = collect(event.target); errors = validateBooking(draft);
    go(Object.keys(errors).length ? 'booking_error' : 'booking_review');
  };
}
function profileForm(state) {
  show(state, state === 'profile_error' ? 'Profile details need attention' : 'Edit your member profile', `
    <p class="intro">Keep your contact details and preferences up to date.</p>${errorSummary()}
    <form id="profile-form" novalidate style="max-width:620px">
    ${field('Display name', 'Member name', '2–40 characters.', profileDraft)}
    ${field('Member email', 'Member email', 'A valid email address with a dotted domain.', profileDraft, 'email')}
    ${field('Updates preference', 'How should we send updates?', 'Type Email, SMS or None.', profileDraft)}
    ${field('Access notes', 'Accessibility notes', 'Optional; at most 80 characters.', profileDraft, 'text', true)}
    <button type="submit">Save member profile</button></form>
    ${actions(link('account', 'Discard changes', true), ...(state === 'profile_error'
      ? [link('profile_edit', 'Clear errors and revise', true)] : []))}`);
  app.querySelector('form').onsubmit = async event => {
    event.preventDefault(); profileDraft = collect(event.target); errors = {};
    if (length(profileDraft['Display name']) < 2 || length(profileDraft['Display name']) > 40)
      errors['Display name'] = 'Display name must contain 2–40 characters.';
    if (!/^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$/.test(profileDraft['Member email']))
      errors['Member email'] = 'Enter a valid member email with a dotted domain.';
    if (!['Email', 'SMS', 'None'].includes(profileDraft['Updates preference']))
      errors['Updates preference'] = 'Updates preference must be Email, SMS or None.';
    if (length(profileDraft['Access notes']) > 80) errors['Access notes'] = 'Access notes must contain at most 80 characters.';
    if (Object.keys(errors).length) return go('profile_error');
    try { await api('profile', { data: profileDraft }); go('profile_saved'); } catch (error) { serviceError(error); }
  };
}
function cancelForm(state) {
  show(state, state === 'cancellation_error' ? 'Cancellation reason needs attention' : 'Request a cancellation', `
    <p class="intro">Booking ${escapeHtml(booking.id)} · Full refund ${money(booking.total_cents)}.</p>
    ${errorSummary()}<form id="cancel-form" novalidate style="max-width:620px">
    ${field('Cancellation reason', 'Why are you cancelling?', 'Required; 5–120 characters.', cancelDraft, 'text', true)}
    <button type="submit">Review cancellation</button></form>
    ${actions(link('booking_confirmed', 'Keep my booking', true), ...(state === 'cancellation_error'
      ? [link('cancellation_details', 'Clear errors and revise', true)] : []))}`);
  app.querySelector('form').onsubmit = event => {
    event.preventDefault(); cancelDraft = collect(event.target); errors = {};
    const size = length(cancelDraft['Cancellation reason']);
    if (size < 5 || size > 120) errors['Cancellation reason'] = 'Cancellation reason must contain 5–120 characters.';
    go(Object.keys(errors).length ? 'cancellation_error' : 'cancellation_review');
  };
}
function serviceError(error) {
  show('service_error', 'Demo service unavailable', `<p role="alert">${escapeHtml(error.message)}</p>
    ${link('home', 'Return home')}`);
}
function go(state) {
  // Repeated rejection submissions still re-render; hashchange alone would miss self loops.
  if (location.hash === `#${state}`) render(state);
  else location.hash = state;
}

async function render(state) {
  const version = ++routeVersion;
  try { store = await api('state'); } catch (error) { serviceError(error); return; }
  if (version !== routeVersion) return;
  switch (state) {
    case 'home':
      errors = {};
      show(state, 'Take the scenic route.', `<div class="hero"><section>
        <p class="intro">Guided adventures for small groups. Walk the ridge, paddle the river, and leave the planning to us.</p>
        ${actions(link('catalogue', 'Explore adventures'), link('booking_details', 'Plan a trip', true))}
        <p>Two adventures. One simple promise: equipment, expert guides and clear prices.</p>
        ${actions(link('policies', 'Our booking promise', true))}</section><div class="landscape" aria-hidden="true">
        <svg viewBox="0 0 500 350"><rect width="500" height="350" fill="#d9dfcb"/>
        <circle cx="370" cy="85" r="42" fill="#d39b67"/><path d="M0 310L145 90L300 310Z" fill="#718575"/>
        <path d="M120 350L315 135L500 350Z" fill="#365e4b"/><path d="M320 350Q210 270 365 240Q460 205 430 180"
        fill="none" stroke="#e7e7ce" stroke-width="24"/></svg></div></div>`);
      break;
    case 'catalogue':
      show(state, 'Find your next adventure', `<p class="intro">Choose a day outside. All equipment and guiding are included.</p>
        <div class="cards"><article class="card"><p class="eyebrow">ON FOOT / 4 HOURS</p><h2>Ridge walk</h2>
        <p>Forest paths, sweeping views and a shared picnic. Moderate walking fitness recommended.</p>
        <p class="price">AUD 80 per traveller</p>${link('ridge', 'Explore Ridge walk')}</article>
        <article class="card"><p class="eyebrow">ON WATER / 3 HOURS</p><h2>River paddle</h2>
        <p>A gentle guided paddle. No previous paddling experience required.</p><p class="price">AUD 120 per traveller</p>
        ${link('river', 'Explore River paddle')}</article></div>
        ${actions(link('booking_details', 'Plan a trip'), link('policies', 'Booking policies', true))}`);
      break;
    case 'ridge': case 'river': {
      const ridge = state === 'ridge';
      show(state, ridge ? 'Ridge walk: above it all' : 'River paddle: go with the flow', `<div class="layout"><section>
        <p class="intro">${ridge ? 'A four-hour guided ridge walk with a picnic.' : 'A three-hour guided river paddle for beginners.'}</p>
        <p class="price">${ridge ? 'AUD 80' : 'AUD 120'} per traveller</p><p>Party size: 1–6 travellers.</p>
        ${actions(link('booking_details', 'Plan this adventure'), link(ridge ? 'river' : 'ridge', 'Compare the other adventure', true))}
        </section><aside><h2>Ready for the day</h2><p>${ridge ? 'Bring sturdy shoes, water and a weatherproof layer. Walking poles are included.'
          : 'Bring sun protection and water. Kayak, paddle and life jacket are included.'}</p>
        ${actions(link('equipment', 'Equipment guide', true), link('policies', 'Booking policies', true))}</aside></div>`);
      break;
    }
    case 'equipment':
      show(state, 'Pack light. We have the rest.', `<div class="cards"><article class="card"><h2>Ridge walk kit</h2>
        <p>Walking poles and a shared picnic are included. Bring sturdy shoes, water and a weatherproof layer.</p>
        ${link('ridge', 'See Ridge walk', true)}</article><article class="card"><h2>River paddle kit</h2>
        <p>Kayak, paddle and life jacket are included. Bring sun protection and water.</p>
        ${link('river', 'See River paddle', true)}</article></div>
        ${actions(link('booking_details', 'Plan a trip'), link('policies', 'Safety and booking policies', true))}`);
      break;
    case 'policies':
      show(state, 'Clear plans. Fair policies.', `<div class="cards"><article class="card"><h2>Booking and prices</h2>
        <p>Ridge walk costs AUD 80 per traveller. River paddle costs AUD 120 per traveller.</p>
        <p>Parties contain 1–6 travellers. A conservation gift of AUD 0–50 is added once, not per traveller.</p>
        <p>Review is a draft. Places are reserved only after final confirmation.</p></article>
        <article class="card"><h2>Cancellation and refunds</h2><p>This synthetic demo offers a full refund for every cancellation,
        including the conservation gift. Cancelling releases every reserved place.</p>
        <p>Reviewing a cancellation does not cancel the booking. Final cancellation is required.</p>
        <p>Each booking receives one refund only.</p></article></div>
        ${actions(link('booking_details', 'Plan a trip'), link('help', 'Get help', true))}`);
      break;
    case 'help':
      show(state, 'A little help before you go', `<div class="cards"><article class="card"><h2>Questions about your trip?</h2>
        <p>Contact hello@trailhead.example.test. This is a fictional contact address.</p>
        <p>Equipment is included in both adventures. Groups are limited to six travellers per booking.</p>
        ${link('equipment', 'What to bring', true)}</article><article class="card"><h2>Change of plans?</h2>
        <p>Use the cancellation option on your booking confirmation. Review the refund before cancelling.</p>
        ${link('policies', 'Read our refund policy', true)}</article></div>
        ${actions(link('catalogue', 'Browse adventures'), link('profile_edit', 'Update member details', true))}`);
      break;
    case 'account':
      show(state, 'Your Trailhead account', `<div class="layout"><section>${rows(store.profile)}
        ${actions(link('profile_edit', 'Edit member profile'))}</section><aside><h2>Your account, your choice</h2>
        <p>Supported update preferences: Email, SMS or None (no updates). Accessibility notes are optional.</p>
        <p>Bookings are separate from your member profile.</p>${link('policies', 'Booking and refund policies', true)}</aside></div>`);
      break;
    case 'booking_details': case 'booking_pickup':
      errors = {};
      if (!['booking_details', 'booking_pickup', 'booking_review', 'booking_error'].includes(app.dataset.state)) {
        draft = { 'Traveller name': '', 'Contact email': '', 'Party size': '2',
          'Adventure': 'Ridge walk', 'Conservation contribution': '0' };
        token = crypto.randomUUID(); booking = null; pickup = 'City visitor centre';
      }
      if (state === 'booking_details') pickup = 'City visitor centre';
      bookingForm(state); break;
    case 'booking_error': bookingForm(state); break;
    case 'booking_review':
      show(state, 'Review your adventure', `<div class="layout"><section>${rows({ ...draft, 'Departure pickup': pickup, 'Unit price': money(prices[draft.Adventure] * 100) })}
        ${rows({ 'Trip subtotal': money(Number(draft['Party size']) * prices[draft.Adventure] * 100),
          'Conservation gift amount': money(cents(draft['Conservation contribution'])), 'Party total': money(total(draft)) })}<p class="notice">Draft only. No places have been reserved.</p>
        ${actions('<button id="confirm">Confirm and reserve places</button>', link('booking_details', 'Revise details', true),
          link('catalogue', 'Abandon booking', true))}</section><aside><h2>One clear total</h2>
        <p>The trip price is multiplied by travellers. The conservation gift is added once.</p>
        <p>Equipment and guiding are included. Full refunds are available in this demo.</p></aside></div>`);
      app.querySelector('#confirm').onclick = async event => {
        event.target.disabled = true;
        try { booking = await api('bookings', { data: draft, token, pickup }); go('booking_confirmed'); }
        catch (error) { serviceError(error); }
      }; break;
    case 'booking_confirmed':
      if (!booking) return go('home');
      show(state, 'Adventure booked', `<p class="notice">Confirmed · Booking ${escapeHtml(booking.id)} · Your places are reserved.</p>
        ${rows({ ...booking.data, 'Party total': money(booking.total_cents), 'Departure pickup': booking.pickup })}
        ${actions(link('cancellation_details', 'Cancel this booking'), link('booking_details', 'Plan another trip', true),
          link('policies', 'Refund policy', true))}`); break;
    case 'profile_edit':
      errors = {};
      if (app.dataset.state !== 'profile_error') profileDraft = { ...store.profile };
      profileForm(state); break;
    case 'profile_error': profileForm(state); break;
    case 'profile_saved':
      show(state, 'Member profile saved', `<p class="notice">Your preferences have been saved to your account.</p>
        ${rows(store.profile)}${actions(link('account', 'View my account'), link('profile_edit', 'Edit profile again', true))}`); break;
    case 'cancellation_details':
      if (!booking) return go('home');
      errors = {}; cancelDraft = {}; cancelForm(state); break;
    case 'cancellation_error': cancelForm(state); break;
    case 'cancellation_review':
      show(state, 'Review your cancellation', `${rows({ 'Booking reference': booking.id,
        'Cancellation reason': cancelDraft['Cancellation reason'], 'Full refund': money(booking.total_cents) })}
        <p class="notice">Your booking is still confirmed. No refund has been issued.</p>
        ${actions('<button id="cancel">Confirm cancellation and refund</button>', link('booking_confirmed', 'Keep my booking', true),
          link('cancellation_details', 'Revise cancellation reason', true))}`);
      app.querySelector('#cancel').onclick = async event => {
        event.target.disabled = true;
        try { await api('cancellations', { booking_id: booking.id, data: cancelDraft }); go('cancellation_done'); }
        catch (error) { serviceError(error); }
      }; break;
    case 'cancellation_done':
      show(state, 'Booking cancelled and refunded', `<p class="notice">Booking ${escapeHtml(booking.id)} is cancelled.
        Your places have been released.</p>${rows({ 'Cancellation reason': cancelDraft['Cancellation reason'],
        'Full refund issued': money(booking.total_cents) })}
        ${actions(link('catalogue', 'Find another adventure'), link('policies', 'Read refund policy', true))}`); break;
    default: go('home');
  }
}
window.addEventListener('hashchange', () => render(location.hash.slice(1) || 'home'));
render(location.hash.slice(1) || 'home');
