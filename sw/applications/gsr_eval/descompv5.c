// Copyright 2026 Universidad Politecnica de Madrid
// SPDX-License-Identifier: Apache-2.0
//
// Author: Blanca Calvo <blanca.calvo@alumnos.upm.es>
// Description: Algorithm to decompose tonic and phasic components of GSR signals

#include <stdio.h>
#include <stdlib.h>
#include <math.h>
/* signal_length comes from input_signal.h (chip sim / native build) */
#ifndef signal_length
#  include "input_signal.h"
#endif
#include "descompv5.h"

#define LAMBDA 1
int shift_mul5(int A_Q, int B_Q) {
    int prod = A_Q * B_Q;
    return (prod >> Q_SCALE);
}

int shift_div5(int num, int den) {
    if (den == 0) return 0;

    // Encontrar la potencia de 2 más cercana (p2)
    int k = 0;
    int p2 = 1;
    // Buscamos p2 <= den < 2*p2
    while ((p2 << 1) > 0 && (p2 << 1) <= den) {
        p2 <<= 1;
        k++;
    }

    int R = den - p2;

    int num_escalado_Q = num << Q_SCALE;
    int resultado_base_Q = num_escalado_Q >> k; // Q14

    int R_temp_Q = R << Q_SCALE;
    int R_over_p2_Q = R_temp_Q >> k;

    int termino_correccion_Q = shift_mul5(R_over_p2_Q, resultado_base_Q);

    int resultado_final_Q = resultado_base_Q - termino_correccion_Q;

    return resultado_final_Q;
}

// Matriz Triangular Superior U
int EDA_U_diag0[signal_length];
int EDA_U_diag1[signal_length - 1];
int EDA_U_diag2[signal_length - 2];

// Multiplicadores de la Matriz Inferior L
int EDA_L_mult_1[signal_length - 1]; // Diagonal -1
int EDA_L_mult_2[signal_length - 2]; // Diagonal -2

int tmp_U0[signal_length];
int tmp_U1[signal_length - 1];
int tmp_U2[signal_length - 2];
int tmp_L1[signal_length - 1];
int tmp_L2[signal_length - 2];


// esto se hace una vez y ya se queda almacenado mientras el sistema esté en uso
void EDA_build_A_and_factorize() {
    int lambda_Q = LAMBDA * ONE_Q;
    int lambda_sq = shift_mul5(lambda_Q, lambda_Q);
    int N = signal_length;

    int diag_1[signal_length];
    int diag_2[signal_length];

    for (int i = 0; i < N; i++) {
        int D2tD2_0= 0;
        if (i == 0 || i == N - 1) D2tD2_0 = 1*ONE_Q;
        else if (i == 1 || i == N - 2) D2tD2_0 = 5*ONE_Q;
        else D2tD2_0 = 6*ONE_Q;
        EDA_U_diag0[i] = ONE_Q + shift_mul5(lambda_sq, D2tD2_0);
    }

    for (int i = 0; i < N - 1; i++) {
        int D2tD2_1=0;
        if (i == 0 || i == N - 2) D2tD2_1 = -2*ONE_Q;
        else D2tD2_1 = -4*ONE_Q;
        EDA_U_diag1[i] = shift_mul5(lambda_sq, D2tD2_1);
        diag_1[i + 1] = EDA_U_diag1[i];
    }

    for (int i = 0; i < N - 2; i++) {
        int D2tD2_2 = 1 * ONE_Q;
        EDA_U_diag2[i] = shift_mul5(lambda_sq, D2tD2_2);
        diag_2[i + 2] = EDA_U_diag2[i];
    }

    for (int i = 0; i < N - 2; i++) {
        int pivote = EDA_U_diag0[i];

        int factor1 = diag_1[i + 1];
        EDA_L_mult_1[i] = shift_div5(factor1, pivote); // comprobar
        EDA_U_diag0[i + 1] -= shift_mul5(EDA_L_mult_1[i], EDA_U_diag1[i]);
        EDA_U_diag1[i + 1] -= shift_mul5(EDA_L_mult_1[i], EDA_U_diag2[i]);

        int factor2 = diag_2[i + 2];
        EDA_L_mult_2[i] = shift_div5(factor2, pivote);
        diag_1[i + 2] -= shift_mul5(EDA_L_mult_2[i], EDA_U_diag1[i]);
        EDA_U_diag0[i + 2] -= shift_mul5(EDA_L_mult_2[i], EDA_U_diag2[i]);
    }

    {
        int i = N - 2;
        int pivote = EDA_U_diag0[i];
        int factor = diag_1[i + 1];
        EDA_L_mult_1[i] = shift_div5(factor, pivote);
        EDA_U_diag0[i + 1] -= shift_mul5(EDA_L_mult_1[i], EDA_U_diag1[i]);
    }
}
void EDA_AdjustMasterMatrix(int n_target, const int mU0[], const int mU1[], const int mU2[], const int mL1[], const int mL2[]) {
    // Copio los valores de la matriz general
    for (int i = 0; i < n_target; i++) {
        tmp_U0[i] = mU0[i];
        if (i < n_target - 1) { tmp_U1[i] = mU1[i]; tmp_L1[i] = mL1[i]; }
        if (i < n_target - 2) { tmp_U2[i] = mU2[i]; tmp_L2[i] = mL2[i]; }
    }

    // recalculo los valores de la matriz en los bordes
    int lambda_sq = shift_mul5(LAMBDA * ONE_Q, LAMBDA * ONE_Q);
    int i_n3 = n_target - 3; // 97
    int i_n2 = n_target - 2; // 98
    int i_n1 = n_target - 1; // 99
    // en la posicion 97 debe ser 1 (lambda)
    tmp_U2[i_n3] = lambda_sq;
    tmp_U1[i_n2] = -shift_mul5(lambda_sq, 2 * ONE_Q);
    int A_N2_N2 = ONE_Q + shift_mul5(lambda_sq, 5 * ONE_Q);
    tmp_U0[i_n2] = A_N2_N2 - shift_mul5(tmp_L1[i_n2-1], tmp_U1[i_n2-1]) - shift_mul5(tmp_L2[i_n2-2], tmp_U2[i_n2-2]);

    int A_N1_N2 = -shift_mul5(lambda_sq, 2 * ONE_Q);
    tmp_L1[i_n2] = shift_div5(A_N1_N2, tmp_U0[i_n2]);
    int A_N1_N1 = ONE_Q + lambda_sq;
    tmp_U0[i_n1] = A_N1_N1 - shift_mul5(tmp_L1[i_n2], tmp_U1[i_n2]) - shift_mul5(tmp_L2[i_n1-2], tmp_U2[i_n1-2]);
}

void forward_elimination_v5(int N, int b[], int l1[], int l2[]) {
    for (int i = 0; i < N - 2; i++) {
        b[i + 1] -= shift_mul5(l1[i], b[i]);
        b[i + 2] -= shift_mul5(l2[i], b[i]);
    }
    {
        int i = N - 2;
        b[i + 1] -= shift_mul5(l1[i], b[i]);;
    }
}


void back_substitution_v5(int N, int b[], int x[], int u0[], int u1[], int u2[]) {

    x[N - 1] = shift_div5(b[N - 1], u0[N - 1]);

    int term = shift_mul5(u1[N - 2],x[N - 1]) ;
    int numerador = (b[N - 2] - term);
    x[N - 2] = shift_div5(numerador, u0[N - 2]);

    for (int i = N - 3; i >= 0; i--) {
    int sum = 0;

    sum += shift_mul5(u1[i] , x[i + 1]);
    sum += shift_mul5(u2[i] , x[i + 2]);
    numerador = (b[i] - sum);
    x[i] = shift_div5(numerador, u0[i]);
}
}


void solve_v5(int N, int Xeda[], int Xtonica[], int Xfasica[], int u0[], int u1[], int u2[], int l1[], int l2[]) {
    int b_temp[N];

    for (int i = 0; i < N; i++) {
        b_temp[i] = Xeda[i];
    }

    forward_elimination_v5(N, b_temp, l1, l2);

    back_substitution_v5(N, b_temp, Xtonica, u0, u1, u2);

    for (int i = 0; i < N; i++) {
        Xfasica[i] = Xeda[i] - Xtonica[i];
    }
}
