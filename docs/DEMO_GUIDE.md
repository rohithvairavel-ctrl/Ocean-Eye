# Present OCEAN-EYE from VS Code

## Start (about one minute)

1. Open `OCEAN-EYE.code-workspace` in VS Code.
2. Press **Ctrl+Shift+B** to run **OCEAN-EYE: Start app**.
3. Open `http://127.0.0.1:5173` in a browser. Use browser full-screen if presenting on a projector.
4. In **Cases & Data**, create a new demo case. Click **Run full investigation**. Wait for the actual processing stage to finish.

## Suggested five-minute walkthrough

1. **Overview:** Point out the **DEMO DATA** badge. Explain that synthetic inputs pass through the same analysis pipeline as uploads.
2. **Satellite Analysis:** Show the input SAR, filtered image and segmentation. The measured area is approximately **42.6 km²**, rather than a dashboard constant. Explain that a dark-region candidate still needs oil confirmation.
3. **Drift & Origin:** Show the particle region and assumed release window. Explain that the 90% region is conditional ensemble spread, not a calibrated probability that the actual source lies there.
4. **AIS Correlation:** Select **AIS · release window** on the map. Click a vessel. Show the primary candidate's slow passage and reporting gap. Switch to **At SAR observation** to show later recorded positions; vessels may have left the scene by then.
5. **Vessel Ranking:** Click **Why this vessel?** Show the measured behavior and weighted contributions. Do not describe the score as guilt or probability. Click **Remove primary candidate** and show that the remaining vessels rerank without changing original evidence.
6. **Dark Vessels:** Show unmatched bright SAR returns and gaps. Explain why neither alone proves a dark vessel.
7. **Evidence Graph** and **Timeline:** Connect the observation, inferred origin, vessel evidence, and the distinction between observed and assumed events.
8. **Reports:** Download the forensic PDF and evidence ZIP. The ZIP contains raw input snapshots, masks, geometry, ranking, timeline, report, and checksums.
9. Optional: change the windage coefficient in **Settings**, rerun, and compare the new run's origin and scores. This demonstrates sensitivity to assumptions.

## Demonstrate file upload

Choose **Import case** and select:

- `datasets/satellite/demo_sar.tif`
- `datasets/ais/demo.csv`
- `datasets/environment/demo.json`

Keep the source type **Synthetic / demonstration** and the prefilled UTC dates. Submit and run the investigation. The results should match the generated demo for the same settings and reference data.

## Show the implementation

In VS Code, open these modules:

- `backend/satellite.py`: segmentation and geometry.
- `backend/drift.py`: hindcast and forecast.
- `backend/ais.py`: data cleaning, behavior evidence, and scoring weights.
- `backend/engine.py`: connected workflow and provenance.
- `tests/test_pipeline.py`: end-to-end and counterfactual checks.

Use **Terminal > Run Task > OCEAN-EYE: Test pipeline** to demonstrate the tests.

## Presentation boundaries

Say: “This is an end-to-end forensic screening prototype with transparent algorithms and reproducible synthetic data.”

Do not claim a validated oil-identification model, real offending vessels, access to government data, operational probability calibration, trained route models, or a complete enterprise deployment. See the capability matrix for the exact installed scope.

## Response intelligence walkthrough

1. Use the workflow ribbon to inspect **Verification**: pollutant UNKNOWN, classifier unavailable, no claimed ML accuracy.
2. Open **Ecological Exposure** and show the first sampled overlap, reference dataset/version and species-data limitation.
3. Open **Response Twin**. Save a no-intervention baseline. Then select dispatch, supply an assumed departure location, speed and delay, and save. Compare the arrival/ready window against the selected forecast horizon. A demo-only example is longitude 80, latitude 12; these coordinates do not establish a real available asset.
4. Add a delayed scenario and review **Attention & Alerts**. Expand the rule trace; explain that warnings are anchored to the historic observation, not a live emergency service.
5. Select a vessel in **Evidence Graph**, open its dossier and inspect factor values, weights, contribution, speed/course observations, gaps and contrary evidence. Exclude that candidate to compare the remaining ranking without changing evidence.
6. In **Reports**, download the original PDF or the supplemental evidence/response ZIP. Scenarios cannot claim removal effectiveness or ecological damage reduction.
7. On the map, enable only the layers needed for the explanation. Use Play/Pause and the time slider. There is no exact known origin point or predicted AIS track.
