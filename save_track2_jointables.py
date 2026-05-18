from track2_join_guide import load_track2, build_lifecycle, join_lifecycle_barn_env, join_packer_cash_prices, join_hedge_vs_packer_period

data = load_track2("Syracuse/Track 2")

# Build the master lifecycle table (18 rows, 34 columns)
lifecycle = build_lifecycle(data)
lifecycle.to_csv("output/lifecycle.csv", index=False)

# Lifecycle + barn environmental stats
lifecycle_env = join_lifecycle_barn_env(data)
lifecycle_env.to_csv("output/lifecycle_with_env.csv", index=False)

# Packer settlements with cash market basis
packer_basis = join_packer_cash_prices(data, track1_dir="Syracuse")
packer_basis.to_csv("output/packer_basis.csv", index=False)

# Hedge P&L proxy via packer period benchmark
hedge_pnl = join_hedge_vs_packer_period(data)
hedge_pnl.to_csv("output/hedge_pnl.csv", index=False)