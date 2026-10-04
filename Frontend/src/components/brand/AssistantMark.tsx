/**
 * The assistant's avatar in a conversation: the StockAI seal (the petrol
 * rounded square with the "S", same as the app icon) rather than a robot
 * glyph. The owner asked for icons that do not read as generic "AI" art; the
 * assistant speaks for the product, so it wears the product's mark.
 */
export function AssistantMark({ size = 14 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size + 4} height={size + 4} viewBox="0 0 44 44" style={{ flexShrink: 0 }}>
      <rect width="44" height="44" rx="10" fill="#0C3A40" />
      <text
        x="22" y="31" textAnchor="middle" fill="#fff"
        style={{ fontFamily: 'Georgia, "Times New Roman", serif', fontWeight: 700, fontSize: 27 }}
      >
        S
      </text>
      <rect x="10" y="35" width="24" height="2.6" rx="1.3" fill="#5EB8AE" />
    </svg>
  )
}
