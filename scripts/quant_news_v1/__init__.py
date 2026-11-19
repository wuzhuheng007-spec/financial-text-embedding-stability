"""Real-time news providers (Benzinga News Stream, Massive Benzinga News REST, Alpaca News).

One server process connects to ONE configured provider and writes a versioned cache; the web app
only reads that cache. Times are kept separately (UTC): provider_created_at, provider_updated_at,
first_seen_at, received_at, available_at. They are provider publication/update times and receipt
times, never event times. Credentials come only from the environment (server secret file).
"""
