# References

## Classification method and features

- C. Setz, B. Arnrich, J. Schumm, R. La Marca, G. Tröster and U. Ehlert,
  "Discriminating Stress From Cognitive Load Using a Wearable EDA Device,"
  *IEEE Transactions on Information Technology in Biomedicine*, 14(2), pp. 410–417, 2010.
  [doi:10.1109/TITB.2009.2036164](https://doi.org/10.1109/TITB.2009.2036164)

  Source of the 16 electrodermal features (`conductance_S_to_features.py`),
  the baseline-relative feature calculation, the leave-one-person-out
  evaluation protocol, and the MIST five-feature linear model preset
  (`MIST_LINEAR_FEATURES`, from the paper's Table I). The paper does not
  specify its smoothing or adaptive-threshold algorithm; the rolling
  median-absolute-deviation threshold implemented here is an explicit
  substitute, not a reproduction of the original detector.

## Public dataset

- A. Hongn, F. Bosch, L. Prado and P. Bonomini (2025), "Wearable Device Dataset
  from Induced Stress and Structured Exercise Sessions (version 1.0.1)," PhysioNet.
  [doi:10.13026/he0v-tf17](https://doi.org/10.13026/he0v-tf17)
  License: Open Data Commons Attribution License v1.0 (stored with the raw data
  in `datasets/raw/physionet/`).

- A. Hongn, F. Bosch, L. E. Prado, J. M. Ferrández and M. P. Bonomini (2025),
  "Wearable Physiological Signals under Acute Stress and Exercise Conditions,"
  *Scientific Data*, 12.
  [doi:10.1038/s41597-025-04845-9](https://doi.org/10.1038/s41597-025-04845-9)

- C. Castellini, F. Schwenker, K. Sharma, E. L. van den Broek and A. Albu-Schaeffer
  (2019), "CASE_Dataset-full," Figshare, dataset.
  [doi:10.6084/m9.figshare.8869157](https://doi.org/10.6084/m9.figshare.8869157)
  (the `CASE_full.zip` download, ~4.55 GB, 30 participants).
  License: CC0.

- K. Sharma, E. L. van den Broek, A. Albu-Schaeffer, F. Schwenker and C. Castellini
  (2019), "A dataset of continuous affect annotations and physiological signals
  for emotion analysis," *Scientific Data*, 6, 196.
  [doi:10.1038/s41597-019-0209-0](https://doi.org/10.1038/s41597-019-0209-0)

  The CASE dataset is the actively used training and test dataset
  (`case_to_timeseries`, `prepare_case`): continuous electrodermal activity at
  1 kHz with continuous affect annotations for 30 participants watching
  validated video clips (scary, amusing, boring, relaxing). Converted to 4 Hz
  in `datasets/training` (sub_01–sub_24) and `datasets/test` (sub_25–sub_30);
  scary clips are labeled stress and boring/relaxing clips not stress (see
  `datasets/README.md`).

