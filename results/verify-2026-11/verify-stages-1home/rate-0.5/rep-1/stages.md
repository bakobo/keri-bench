120 warm verifications of 259 attempts: 120 accepted, 139 refused {'e.state.pending.registry.r': 139}, 0 accepted beside another and left out, and the first from each holder left out.

| rollup | p50 ms | p99 ms | mean ms | share |
|---|---|---|---|---|
| issuer key state | 159.65 | 361.23 | 166.50 | 52.2% |
| status | 41.22 | 77.33 | 41.78 | 13.1% |
| holder binding | 77.46 | 127.47 | 79.33 | 24.9% |
| admit receipt | 1.33 | 3.80 | 1.51 | 0.5% |
| everything else | 27.90 | 75.89 | 29.67 | 9.3% |
| total | 311.16 | 521.14 | 318.78 | |

| stage | calls | p50 ms | p99 ms | mean ms | share |
|---|---|---|---|---|---|
| meet | 0 | 0.00 | 0.00 | 0.00 | 0.0% |
| key_state.holder | 1 | 77.20 | 127.27 | 79.03 | 24.8% |
| key_state.issuer | 2 | 159.65 | 361.23 | 166.50 | 52.2% |
| signatures | 1 | 0.24 | 1.01 | 0.29 | 0.1% |
| tel.fetch | 1 | 15.01 | 26.35 | 16.49 | 5.2% |
| tel.vet | 4 | 26.33 | 57.18 | 25.29 | 7.9% |
| receipt | 1 | 1.33 | 3.80 | 1.51 | 0.5% |

| heti call | p50 ms | p99 ms | mean ms |
|---|---|---|---|
| admit | 195.56 | 300.15 | 198.54 |
| current | 105.97 | 301.47 | 119.75 |

First meetings (OOBI resolution and KEL replay), ms: lockbox-open 127.17, c000000 (ELmjGqEY-3IPmDY5EYqMlZWgszTeS6LxhQXm36MuKQNi) 171.39
