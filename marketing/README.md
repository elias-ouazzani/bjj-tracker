# Marketing page

Static one-page landing site for Strain. Matches the app's "Strain" design
system (tokens mirrored from `main.py`) so the marketing page and the app read
as one product.

- `index.html` — the whole page, self-contained (only external dependency is
  the Hanken Grotesk web font from Google Fonts, same as the app).

## Before it goes live

Replace the placeholder app URL. There are **three** links pointing at
`https://app.example.com/` (nav "Log in", hero "Start training", final CTA).
Change all three to `https://app.<your-domain>` once the app's custom domain is
mapped and serving.

## Deploy (Cloudflare Pages)

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** →
   **Connect to Git** → pick this repo.
2. Build settings:
   - Framework preset: **None**
   - Build command: *(leave empty)*
   - Build output directory: `marketing`
3. Deploy. Cloudflare gives you a `*.pages.dev` URL to preview.
4. **Custom domains** tab → add your apex (`<your-domain>`) and/or `www`.
   Because the domain's DNS is already on Cloudflare, this wires up
   automatically (it adds the records + TLS for you).

The apex/`www` serves this page; `app.<your-domain>` is the separate Cloud Run
mapping for the actual app.
