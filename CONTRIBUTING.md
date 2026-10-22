# Contributing

Thanks for helping. A few things keep this project workable:

1. **Open an issue first** for anything bigger than a small fix, so we can agree on
   the approach before you spend time on it.
2. **Run every self-check** before you send a change:

   ```powershell
   .\scripts\dev.ps1 -Test
   ```

   They need no network and no TallyPrime (a simulated Tally stands in), and they
   use temporary data folders. Add or update a check for what you change; the tests
   are plain Python scripts with a `check(...)` helper, no pytest.
3. **Never commit real books.** No real company, party, GSTIN, bank account, invoice
   or screenshot of real data - in code, tests, fixtures, docs or images. Use the
   fictional sample company (`tools/generate_sample_data.py`, `tools/synth_exim.py`,
   `tools/synth_invoice.py`) or obviously invented values. No keys, tokens or
   `.env`/`deploy.env`/`office.env` files either.
4. **Keep the security design** ([SECURITY.md](SECURITY.md)): the Keeper only reads
   Tally, books are sealed before they leave, updates are signed, the local server
   guards Host and Origin. Changes that weaken one of these need a strong reason
   written down in the pull request.
5. **Tally's name** describes compatibility only ("posts to Tally"); do not use it
   as, or in, the name of this project or its modules.
6. Match the code around you: its naming, its comment style (short, plain sentences
   that say why), and its habit of using the standard library before a new
   dependency.

By sending a contribution you agree it is licensed under the project's licence,
the GNU AGPL-3.0 (see [LICENSE](LICENSE)).
