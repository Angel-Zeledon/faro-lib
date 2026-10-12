import { LandingHome, homeMetadata } from '@/components/landing/homeRoute'

// /en — the home page in English (Spanish: /). Same page, same structured
// data, from the English catalogue.
export const metadata = homeMetadata('en')

export default function Home() {
  return <LandingHome lang="en" />
}
