import './globals.css'

export const metadata = {
  title: 'AgentForge Studio',
  description: 'Local AI app builder',
}

export default function RootLayout({ children }) {
  return (
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        {/* Inter carries the whole system. It is what `--font-display` and
            `--font-body` already ask for first, and it is the closest thing
            on the web to San Francisco — same humanist grotesque skeleton,
            same tight spacing at UI sizes. Apple hardware never downloads it:
            the token lists `-apple-system` right after, so macOS and iOS use
            the real SF and only Windows and Linux fetch the file.

            Archivo used to be loaded here and was never named by any rule, so
            the interface fell through to Segoe UI on Windows while the CSS
            said Inter. The mono stays the platform's own; nothing is
            downloaded for it. */}
        <link rel="stylesheet"
              href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" />
      </head>
      <body>{children}</body>
    </html>
  )
}
