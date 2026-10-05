// The landing's contact channels, shared by the home page and its subpages.
//
// E.164 without the '+', which is what wa.me expects.
export const CONTACT_WHATSAPP = '50671862820'
// The one address every "email us" link on the landing uses: sales and general
// contact alike (owner's team, 2026-10-01). The in-app dialog reads its own
// CONTACT_EMAIL from the deployment's configuration, not from this file.
export const CONTACT_EMAIL = 'contacto@stockai.es'
export const CONTACT_PHONE_HREF = 'tel:+50671862820'
export const CONTACT_PHONE_LABEL = '+506 7186 2820'

export const waHref = (text: string) => `https://wa.me/${CONTACT_WHATSAPP}?text=${encodeURIComponent(text)}`

// `body` prefills the message itself — the pricing calculator uses it to hand
// over the estimate the visitor just built, so nobody has to retype it.
export const mailHref = (subject?: string, body?: string) => {
  const params = [
    subject ? `subject=${encodeURIComponent(subject)}` : '',
    body ? `body=${encodeURIComponent(body)}` : '',
  ].filter(Boolean).join('&')
  return `mailto:${CONTACT_EMAIL}${params ? `?${params}` : ''}`
}
