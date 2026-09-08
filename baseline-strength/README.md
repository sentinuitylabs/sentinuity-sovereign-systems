# Baseline Strength — Signed Release 2026-09-08

A coded rebuild of the supplied Canva website, preserving its information sequence while replacing flattened graphic text with responsive HTML/CSS and a more mature Sentinuity-derived visual hierarchy.

## Final brand/media sign-off
- Original supplied 1440×1440 Baseline Strength brand image is now the primary hero mark and favicon.
- Header uses the same source image in a compact cropped treatment so the identity remains legible without shrinking the full lockup.
- The supplied 97-second visual sequence is embedded immediately after the opening hero as the brand film, before the detailed experience/method sections.
- Production video is a high-quality H.264/AVC web encode (1280×720, 30 fps, fast-start) for reliable browser playback; the visual sequence is unchanged.
- Video is intentionally `preload="metadata"` rather than autoplaying, protecting mobile load time while preserving full-quality playback on demand.

## Stack
- Dependency-free HTML + CSS + JavaScript
- Node-only verification/build scripts
- Cloudflare Pages deployment
- Build command: `npm run verify`
- Output directory: `dist`

## Local verification
```powershell
npm run verify
```
No `npm install` is required.

## Original flow preserved
1. **Forge Your Foundation** — positioning + free consultation
2. **Baseline in Motion** — original full visual brand sequence
3. **Experience** — Kevin Levrone mentorship, 10,000+ rehab hours, Australian Institute of Fitness certification
4. **Meet Your Coach** — longevity, foundational strength, mobility/stability, Melbourne southeast
5. **Blueprint** — rehabilitation, mobility, longevity, personalisation, consultation, tailored solutions
6. **Online Coaching** — $100 / $180 / $250 original tier structure
7. **Ready to Build Your Baseline?** — contact + social close

## Production deployment — same infrastructure pattern as Substriva
1. Create a GitHub repository, e.g. `baselinestrength`.
2. Push this release folder to it.
3. Cloudflare Dashboard → **Workers & Pages** → **Create application** → **Pages** → connect the GitHub repo.
4. Build command: `npm run verify`
5. Build output directory: `dist`
6. Deploy and test the generated `*.pages.dev` URL before touching production DNS.
7. Add `baselinestrength.com` under the Pages project → **Custom domains**.
8. Add `www.baselinestrength.com` and redirect it to the apex domain.

## Canva → Cloudflare cutover
Do not break the live site first.

1. Deploy the Cloudflare Pages preview and test it completely.
2. Add `baselinestrength.com` to Cloudflare as a DNS zone.
3. Copy/import all current DNS records. **Preserve email MX/TXT records, including SPF/DKIM/DMARC, before changing nameservers.**
4. At the domain registrar (if Namecheap, Domain List → Manage → Nameservers), replace the current nameservers with the two nameservers Cloudflare assigns to `baselinestrength.com`.
5. Wait for Cloudflare to show the zone as active.
6. Pages → your project → Custom domains → attach `baselinestrength.com`.
7. Attach `www.baselinestrength.com`; configure a 301 redirect to `https://baselinestrength.com`.
8. Remove obsolete Canva A/CNAME records only after Cloudflare Pages shows the custom domain as active and HTTPS works.
9. Confirm the consultation email, social links, mobile view and desktop view.

The registrar can stay at Namecheap; moving nameservers to Cloudflare does **not** transfer ownership of the domain.

## Integrity
`SHA256SUMS.txt` contains a source manifest created after `npm run verify` passes. The outer ZIP has its own `.sha256` file alongside it.


## 2026-09-08 coach portrait + mobile alignment sign-off
- Replaced `public/assets/coach-portrait.jpg` (and built copy) with the clearer 917×1284 supplied coach image.
- Fixed mobile sticky-header brand mark being hidden by an over-broad `.brand span` rule.
- Stacked hero CTAs on narrow screens so `See the method` cannot clip beyond the viewport.
- Tightened narrow-screen hero heading sizing to remove right-edge pressure.
- Added safe wrapping for long contact links.
- Removed duplicated `X` label from the social link.
