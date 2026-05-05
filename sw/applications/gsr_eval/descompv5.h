// Copyright 2026 Universidad Politecnica de Madrid
// SPDX-License-Identifier: Apache-2.0
//
// Author: Blanca Calvo <blanca.calvo@alumnos.upm.es>
// Description: Algorithm to decompose tonic and phasic components of GSR signals

#ifndef DESCOMPV5_H
#define DESCOMPV5_H

#ifndef signal_length
#error "signal_length must be defined before including descompv5.h. Include input_signal.h first, or define it via -Dsignal_length=N."
#endif

#define Q_SCALE       14
#define ONE_Q         (1 << Q_SCALE)

void EDA_build_A_and_factorize();
void forward_elimination_v5(int N, int b[], int l1[], int l2[]);
void back_substitution_v5(int N, int b[], int x[], int u0[], int u1[], int u2[]);
void EDA_AdjustMasterMatrix(int n_target, const int mU0[], const int mU1[], const int mU2[], const int mL1[], const int mL2[]);
void solve_v5(int N, int Xeda[], int Xtonica[], int Xfasica[], int u0[], int u1[], int u2[], int l1[], int l2[]);

#endif
