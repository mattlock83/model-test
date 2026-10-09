const app = document.querySelector('#app');
const allowExtraSeat = new URLSearchParams(window.location.search).get('bug') === 'seats';
let booking = { name: '', email: '', seats: '1' };

// Keep the opt-in defect enabled when a journey navigates between documents.
const siteLink = (path) => {
  const url = new URL(path, window.location.origin);
  if (allowExtraSeat) url.searchParams.set('bug', 'seats');
  return url.pathname + url.search + url.hash;
};
document.querySelectorAll('.site-header a').forEach((link) => {
  link.href = siteLink(link.getAttribute('href'));
});

const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[character]);

function artwork(className = 'poster', compact = false) {
  return `<svg class="${className}" viewBox="0 0 500 490" ${compact ? 'preserveAspectRatio="xMidYMid slice"' : ''} aria-hidden="true" focusable="false">
    <rect width="500" height="490" fill="#ecc0bd"/>
    <path d="M70 490V320C70 207 150 110 260 110C375 110 440 200 440 306V490Z" fill="#dc5934"/>
    <path d="M129 490V321C129 240 180 170 260 170C341 170 381 235 381 307V490Z" fill="#f3e7cd"/>
    <path d="M189 490V321C189 274 214 232 260 232C306 232 322 271 322 309V490Z" fill="#304cc7"/>
    <path d="M250 490V329C250 315 258 300 272 300C288 300 291 317 291 330V490Z" fill="#ecc0bd"/>
    <circle cx="114" cy="111" r="52" fill="#304cc7"/>
    <path d="M402 56L411 83L437 91L411 100L402 127L393 100L366 91L393 83Z" fill="#202825"/>
    <path d="M42 354L47 368L62 374L47 379L42 394L37 379L22 374L37 368Z" fill="#202825"/>
    <path d="M55 448C97 423 119 454 151 437M347 54C321 41 302 57 288 45" fill="none" stroke="#202825" stroke-width="2"/>
    <text x="26" y="34" fill="#202825" font-family="Arial, sans-serif" font-size="9" font-weight="700" letter-spacing="2">FIELDNOTES / STUDY NO. 01</text>
    <text x="475" y="470" fill="#202825" font-family="Arial, sans-serif" font-size="9" font-weight="700" text-anchor="end" letter-spacing="1.5">MADE SLOWLY, BY HAND</text>
  </svg>`;
}

function summary() {
  return `<aside class="summary-card" aria-label="Workshop information">
    ${artwork('summary-art', true)}
    <div class="summary-body">
      <p class="eyebrow">THE SATURDAY SESSIONS</p>
      <h2>An introduction<br>to printmaking.</h2>
      <p class="summary-description">Carve a little, roll some ink, and take home a print that’s completely yours.</p>
      <div class="summary-row"><span>When</span><strong>Saturday · 10 am–12 pm</strong></div>
      <div class="summary-row"><span>Where</span><strong>Fieldnotes studio</strong></div>
      <div class="summary-row"><span>Bring</span><strong>Just your curiosity</strong></div>
      <div class="summary-price"><strong>$45</strong><span>AUD per person · Materials included</span></div>
    </div>
  </aside>`;
}

function steps(active) {
  return `<div class="steps" aria-label="Booking progress">
    <span ${active === 'details' ? 'class="active" aria-current="step"' : ''}>01 Details</span>
    <span class="step-line" aria-hidden="true"></span>
    <span ${active === 'review' ? 'class="active" aria-current="step"' : ''}>02 Review</span>
    <span class="step-line" aria-hidden="true"></span>
    <span ${active === 'confirmed' ? 'class="active" aria-current="step"' : ''}>03 Create</span>
  </div>`;
}

function show(markup, state) {
  app.innerHTML = markup;
  app.dataset.state = state;
  const titles = { home: 'A little room to make', workshops: 'Workshops', studio: 'Our studio', visit: 'Visit us' };
  document.title = `Fieldnotes — ${titles[state] || 'Workshop booking'}`;
  document.querySelectorAll('.site-nav a').forEach((link) => {
    if (link.dataset.page === state) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  if (state === 'home' && window.location.hash) {
    history.replaceState(null, '', window.location.pathname + window.location.search);
  }
  window.scrollTo(0, 0);
}

function home() {
  show(`<section class="home" aria-labelledby="page-title">
    <div class="hero-copy">
      <p class="eyebrow"><span class="eyebrow-line" aria-hidden="true"></span> THE SATURDAY SESSIONS · NO. 01</p>
      <h1 id="page-title">Make something <br>by hand</h1>
      <p class="hero-description">A slow morning of printmaking. A little ink, a good conversation, and something lovely to take home.</p>
      <button class="button" id="book-place">Book a place <span class="arrow" aria-hidden="true">↗</span></button>
      <div class="workshop-facts">
        <div><span class="fact-label">The time</span><span class="fact-value">Saturday · 10 am</span></div>
        <div><span class="fact-label">The space</span><span class="fact-value">Melbourne studio</span></div>
        <div><span class="fact-label">The price</span><span class="fact-value">$45 per person</span></div>
      </div>
    </div>
    <div class="poster-wrap">
      ${artwork()}
      <div class="poster-tag" aria-label="All curious humans welcome">ALL CURIOUS<br>HUMANS<br>WELCOME</div>
      <div class="poster-caption"><strong>A little imperfect. Entirely yours.</strong><span>Linocut on paper, 2026</span></div>
    </div>
  </section>`, 'home');
  document.querySelector('#book-place').addEventListener('click', () => form());
}

function validate(values) {
  const errors = {};
  if (values.name.length < 2 || values.name.length > 60) errors.name = 'Full name must be between 2 and 60 characters.';
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email)) errors.email = 'Enter a valid email address, such as alex@example.com.';
  // Opt-in demo defect: the advertised limit stays at four while five gets through.
  const maximum = allowExtraSeat ? 5 : 4;
  if (!/^\d+$/.test(values.seats) || !Number.isInteger(Number(values.seats)) || Number(values.seats) < 1 || Number(values.seats) > maximum) {
    errors.seats = 'Seats must be a whole number from 1 to 4.';
  }
  return errors;
}

function form(errors = {}) {
  const hasErrors = Object.keys(errors).length > 0;
  const invalid = (field) => errors[field] ? ' aria-invalid="true"' : '';
  show(`<section class="booking-layout" aria-labelledby="page-title">
    <div class="booking-content">
      <button class="text-button back-link" id="back-home"><span aria-hidden="true">←</span> Back to home</button>
      ${steps('details')}
      <h1 id="page-title">Book your workshop</h1>
      <p class="intro">Save a little space for yourself. Or bring a friend along — there’s room for up to four.</p>
      ${hasErrors ? `<div class="error-summary" role="alert"><h2>Check your details</h2><ul>${Object.entries(errors).map(([field, message]) => `<li id="${field}-error">${escapeHtml(message)}</li>`).join('')}</ul></div>` : ''}
      <form class="booking-form" id="booking-form" novalidate>
        <div class="field">
          <label for="full-name">Full name</label>
          <input id="full-name" name="name" type="text" autocomplete="name" required value="${escapeHtml(booking.name)}" placeholder="Alex Taylor" aria-describedby="name-help${errors.name ? ' name-error' : ''}"${invalid('name')}>
          <p class="field-help" id="name-help">Between 2 and 60 characters.</p>
        </div>
        <div class="field">
          <label for="email-address">Email address</label>
          <input id="email-address" name="email" type="email" autocomplete="email" required value="${escapeHtml(booking.email)}" placeholder="alex@example.com" aria-describedby="email-help${errors.email ? ' email-error' : ''}"${invalid('email')}>
          <p class="field-help" id="email-help">Use a valid address, such as alex@example.com.</p>
        </div>
        <div class="field field-seats">
          <label for="seats">Seats</label>
          <input id="seats" name="seats" type="text" inputmode="numeric" required value="${escapeHtml(booking.seats)}" aria-describedby="seats-help${errors.seats ? ' seats-error' : ''}"${invalid('seats')}>
          <p class="field-help" id="seats-help">1–4 people · $45 each</p>
        </div>
        <div class="form-actions"><button class="button" type="submit">Review booking <span class="arrow" aria-hidden="true">→</span></button></div>
        <p class="privacy-note">You’ll review everything before confirming. No payment required.</p>
      </form>
    </div>
    ${summary()}
  </section>`, hasErrors ? 'error' : 'form');
  document.querySelector('#back-home').addEventListener('click', home);
  document.querySelector('#booking-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    booking = {
      name: String(fields.get('name') ?? '').trim(),
      email: String(fields.get('email') ?? '').trim(),
      seats: String(fields.get('seats') ?? '').trim(),
    };
    const problems = validate(booking);
    if (Object.keys(problems).length) form(problems);
    else review();
  });
}

function review() {
  show(`<section class="booking-layout" aria-labelledby="page-title">
    <div class="booking-content">
      <div class="back-link" aria-hidden="true">&nbsp;</div>
      ${steps('review')}
      <h1 id="page-title">Review your booking</h1>
      <p class="intro">A good morning is taking shape. Check your details, then we’ll save your place.</p>
      <dl class="review-details">
        <div><dt>Full name</dt><dd id="review-name">${escapeHtml(booking.name)}</dd></div>
        <div><dt>Email address</dt><dd id="review-email">${escapeHtml(booking.email)}</dd></div>
        <div><dt>Seats</dt><dd id="review-seats">${escapeHtml(booking.seats)}</dd></div>
      </dl>
      <div class="review-total"><span>Total · AUD</span><strong id="review-total">$${Number(booking.seats) * 45}</strong></div>
      <div class="review-actions">
        <button class="button button-secondary" id="edit-details">Edit details</button>
        <button class="button" id="confirm-booking">Confirm booking <span class="arrow" aria-hidden="true">→</span></button>
      </div>
      <p class="privacy-note">This is a demo booking. No payment or email is sent.</p>
    </div>
    ${summary()}
  </section>`, 'review');
  document.querySelector('#edit-details').addEventListener('click', () => form());
  document.querySelector('#confirm-booking').addEventListener('click', confirmed);
}

function confirmed() {
  show(`<section class="booking-layout" aria-labelledby="page-title">
    <div class="booking-content confirmed-content">
      ${steps('confirmed')}
      <div class="success-icon" aria-hidden="true">✓</div>
      <h1 id="page-title">You’re on the list</h1>
      <div class="confirmation-status" role="status">Thanks, <strong>${escapeHtml(booking.name)}</strong>. We’ve saved <strong>${escapeHtml(booking.seats)} ${Number(booking.seats) === 1 ? 'seat' : 'seats'}</strong> for your Saturday printmaking workshop.<br>Booking email: <strong>${escapeHtml(booking.email)}</strong>.</div>
      <p class="confirmation-note"><strong>See you at the studio.</strong><br>Saturday, 10 am–12 pm · All materials included.<br>Wear something you don’t mind getting a little inky.</p>
      <button class="button" id="start-again">Start again <span class="arrow" aria-hidden="true">↗</span></button>
      <p class="privacy-note">Demo complete. Your details stay in this browser session.</p>
    </div>
    ${summary()}
  </section>`, 'confirmed');
  document.querySelector('#start-again').addEventListener('click', () => {
    booking = { name: '', email: '', seats: '1' };
    home();
  });
}

function workshops() {
  show(`<section class="info-page" aria-labelledby="page-title">
    <p class="eyebrow">MAKE A MORNING OF IT</p>
    <h1 id="page-title">Workshops</h1>
    <p class="intro page-intro">Small classes, patient guidance, and something made by you. Every session is designed for beginners.</p>
    <article class="workshop-listing">
      <div class="listing-art">${artwork()}</div>
      <div class="listing-copy"><p class="eyebrow">THE SATURDAY SESSIONS · NO. 01</p>
        <h2>Introduction to printmaking</h2>
        <p>Learn to carve a simple linocut, roll your first layer of ink, and print a small edition to take home.</p>
        <dl class="page-facts"><div><dt>When</dt><dd>Saturday, 10 am–12 pm</dd></div>
          <div><dt>Price</dt><dd>AUD 45 per person</dd></div>
          <div><dt>Included</dt><dd>All tools, paper and ink</dd></div>
          <div><dt>Group size</dt><dd>Book 1–4 places</dd></div></dl>
        <a class="button" href="${siteLink('/#book')}">Book the printmaking workshop <span aria-hidden="true">↗</span></a>
      </div>
    </article>
    <div class="page-callout"><p>Curious about where we make?</p><a href="${siteLink('/studio.html')}">Explore our studio <span aria-hidden="true">→</span></a></div>
  </section>`, 'workshops');
}

function studio() {
  show(`<section class="info-page" aria-labelledby="page-title">
    <p class="eyebrow">A LITTLE ROOM TO MAKE</p>
    <h1 id="page-title">Our studio</h1>
    <p class="intro page-intro">A shared table in Melbourne. A shelf full of ink. Space to try something you haven’t tried before.</p>
    <div class="info-grid">
      <article class="info-card"><span class="card-number">01 / THE PEOPLE</span><h2>Beginners belong here.</h2><p>No drawing experience is needed. Our tutors guide you through each step, from your first sketch to the final print.</p></article>
      <article class="info-card"><span class="card-number">02 / THE MATERIALS</span><h2>Everything is ready.</h2><p>We provide the carving tools, paper and ink. Just bring your curiosity and clothes you don’t mind getting a little inky.</p></article>
      <article class="info-card"><span class="card-number">03 / THE PACE</span><h2>Make time to make.</h2><p>Our Saturday sessions run from 10 am to noon. Small groups leave room for questions, conversation and a second attempt.</p></article>
    </div>
    <div class="page-callout"><p>Find your way to the shared table.</p><a class="button button-secondary" href="${siteLink('/visit.html')}">Plan your visit <span aria-hidden="true">→</span></a></div>
  </section>`, 'studio');
}

function visit() {
  show(`<section class="info-page" aria-labelledby="page-title">
    <p class="eyebrow">WE’LL SAVE YOU A SEAT</p>
    <h1 id="page-title">Visit us</h1>
    <p class="intro page-intro">A quiet corner for a colourful Saturday. Here’s what to know before your workshop.</p>
    <div class="visit-grid">
      <article class="visit-card"><h2>Fieldnotes, Melbourne</h2><p class="studio-address">12 Paper Lane<br>Melbourne VIC 3000</p><p class="field-help">Fictional address for this demo studio.</p><dl class="page-facts"><div><dt>Workshop hours</dt><dd>Saturday, 10 am–12 pm</dd></div><div><dt>Arrival</dt><dd>Please arrive 10 minutes early</dd></div><div><dt>Access</dt><dd>Step-free entry and an accessible bathroom</dd></div></dl></article>
      <div class="visit-notes"><h2>A few useful things.</h2><p><strong>Bring yourself.</strong> All workshop materials are included. Wear clothes that can handle a little ink.</p><p><strong>Come together.</strong> A single booking can include up to four people.</p><p><strong>Start with a session.</strong> Choose your workshop before reserving a place.</p><a class="button" href="${siteLink('/workshops.html')}">Browse workshops <span aria-hidden="true">→</span></a></div>
    </div>
  </section>`, 'visit');
}

const pages = { '/workshops.html': workshops, '/studio.html': studio, '/visit.html': visit };
if (pages[window.location.pathname]) pages[window.location.pathname]();
else if (window.location.hash === '#book') form();
else home();
