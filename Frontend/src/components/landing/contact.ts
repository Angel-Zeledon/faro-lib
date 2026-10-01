// The landing's contact channels, shared by the home page and its subpages.
//
// E.164 without the '+', which is what wa.me expects.
export const CONTACT_WHATSAPP = '50671862820'
// The one address every "email us" link on the landing uses — the same one the
// production deployment sets as CONTACT_EMAIL for the in-app dialog.
export const CONTACT_EMAIL = 'angel.zeledon.fernandez@gmail.com'
export const CONTACT_PHONE_HREF = 'tel:+50671862820'
export const CONTACT_PHONE_LABEL = '+506 7186 2820'

export const waHref = (text: string) => `https://wa.me/${CONTACT_WHATSAPP}?text=${encodeURIComponent(text)}`
export const mailHref = (subject?: string) =>
  `mailto:${CONTACT_EMAIL}${subject ? `?subject=${encodeURIComponent(subject)}` : ''}`
