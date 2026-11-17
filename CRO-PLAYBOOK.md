# CRO playbook - what to test at each leaking stage

The brief tells you which stage is leaking and what it costs. This tells you what to try.
Every idea is a hypothesis, not a fix: add it to the backlog, size it, and let the test decide.

Score each idea 1-10 on **Impact** (how much of the leak it could close), **Confidence** (how
sure you are it will work, from evidence like session recordings, reviews or support tickets)
and **Ease** (time and money to ship). `cro.py backlog` multiplies that by the dollars leaking.

## Session to cart (product page)

Look first: session recordings and heatmaps on your top 5 product pages, mobile first.

- The price, the main image and the add-to-cart button are all visible on a phone without scrolling.
- Shipping cost and delivery date are stated on the product page, not discovered at checkout.
- Real photos in use (scale, on a person, in a room), plus a short video for the hero product.
- Reviews with photos near the button; star rating under the title.
- Size, fit or "which one is right for me" help where returns are high.
- Stock and variant errors handled: no dead "sold out" pages from ads.
- Page speed on mobile (the ad landing page especially).
- The ad and the page say the same thing (offer, price, image). A mismatch shows up here first.

## Cart to checkout

Look first: what changed on the cart this week (an app, a shipping rate, a theme update).
A sudden drop here is usually something that broke, not shopper behaviour.

- Shipping cost and free-shipping threshold shown in the cart, with "you're $X away".
- One clear checkout button; express pay (Shop Pay, Apple Pay, PayPal) visible in the cart.
- No surprise at the cart: fees, taxes, or a discount that disappears.
- Upsells in the cart help AOV but can hurt this rate - test them, don't assume.
- Cart drawer vs cart page.
- Discount code box: a visible empty box sends people off to search for codes.

## Checkout to order

Look first: payment failures and error messages in your platform's checkout reports.

- Every payment method your buyers use is on (express wallets, buy now pay later).
- Guest checkout allowed; no forced account.
- Trust at the pay step: returns policy, security, a contact line.
- Address autocomplete and fewer fields.
- Abandoned checkout emails and SMS (timing: 1 hour, 24 hours).
- International: currency, duties and shipping shown before the pay step.

## AOV (if traffic and conversion are healthy)

- Free-shipping threshold set just above your current AOV.
- Bundles and "complete the set" on the product page.
- Quantity breaks.
- Post-purchase one-click upsell.

## Traffic (if the funnel is healthy and you need more at the top)

The funnel numbers tell you whether more traffic will convert. Fix the leaking stage first:
paying for traffic into a leaking stage buys you more of the leak.

## Test discipline

- One change per test, or you will not know what worked.
- Size it first (`cro.py size`); run whole weeks (7, 14, 21 days).
- Do not stop early because it looks good (`cro.py check` will say "keep running").
- Write down the hypothesis before the test, not after.
