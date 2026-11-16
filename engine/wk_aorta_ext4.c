/* wk_aorta.c -- three-element Windkessel (RCR) outlets + pulsatile inlet.
 *
 * MODEL
 *   P(t)     = R1*Q(t) + Pc(t) + P_ven
 *   C dPc/dt = Q(t) - Pc(t)/R2
 * Implicit discretization (once per time step):
 *   Pc^{n+1} = (Pc^n + dt*Q/C) / (1 + dt/(R2*C))
 *
 * UNITS
 *   Q [m^3/s] = -sum(F_FLUX)/rho   (F_FLUX positive into domain; outlet negative)
 *   R [Pa*s/m^3], C [m^3/Pa], P [Pa] (Fluent gauge pressure)
 *
 * SHOCK SETTINGS (hemorrhagic shock)
 *   MAP = 60 mmHg, CO = 3.0 L/min, HR = 120 bpm, tau = 1.2 s
 *   RT  = 1.2 mmHg*s/mL = 1.60e8 Pa*s/m^3
 *   flow split 33 / 29 / 38 %  (arch / renal-SMA / iliac)
 *
 * NOTE: pure ASCII only (Fluent UDF builder reads with a GBK codec).
 *       DEFINE_ADJUST must be hooked: Define > User-Defined > Function Hooks > Adjust.
 */
#include "udf.h"

#define RHO 1050.0
#define P_VEN 0.0

typedef struct {
    real R1, R2, C;   /* SI units */
    real Pc;          /* state variable, Pa */
    real Q;           /* last outlet volume flow, m^3/s, positive = leaving */
    int  nids;        /* number of face zones in this outlet group */
    int  ids[8];      /* face zone ids */
} WK;

/* shock: RT = 1.60e8; split 33/29/38; R1 = 0.05 Rj; C = tau/Rj
 * Pc starts at 0 so outlet pressure starts near zero (no reverse-flow startup). */
static WK wk_arch = {2.42e7, 4.61e8, 2.48e-9, 7.61e3, 0.0, 6, {1082, 1081, 1077, 1076, 1070, 1069}};
static WK wk_renal = {2.76e7, 5.24e8, 2.18e-9, 7.60e3, 0.0, 4, {1075, 1074, 1073, 1072}};
static WK wk_iliac = {2.11e7, 4.00e8, 2.85e-9, 7.60e3, 0.0, 2, {1083, 1071}};

#define T_CYCLE 0.5000        /* 120 bpm */
#define Q_MEAN  50.00e-6      /* 3 L/min */
#define A_INLET 1.026e-3      /* 10.26 cm^2 */

static const real WK_A[6]  = {1.71, 1.27, 0.71, 0.256, 0.009, 0.073};
static const real WK_PH[6] = {-1.125, -2.266, 2.88, 1.791, 0.942, 2.746};

static real inlet_flow(real t)
{
    real q = 1.0;
    real w = 2.0 * M_PI / T_CYCLE;
    int k;
    for (k = 0; k < 6; k++)
        q += WK_A[k] * cos((k + 1) * w * t + WK_PH[k]);
    if (q < 0.0) q = 0.0;
    return Q_MEAN * q;
}

/* global sum of F_FLUX over one face thread (parallel safe) */

static real thread_flux(Thread *th)
{
    real s = 0.0;
    face_t f;
    begin_f_loop(f, th) { s += F_FLUX(f, th); } end_f_loop(f, th)
#if RP_NODE
    s = PRF_GRSUM1(s);
#endif
    return s;
}

static real group_flux(Domain *d, WK *w)
{
    real s = 0.0;
    int i;
    for (i = 0; i < w->nids; i++) {
        Thread *th = Lookup_Thread(d, w->ids[i]);
        face_t f;
        if (th == NULL) continue;
        begin_f_loop(f, th) { s += F_FLUX(f, th); } end_f_loop(f, th)
    }
#if RP_NODE
    s = PRF_GRSUM1(s);
#endif
    return s;
}

/* ---------- immersed-boundary ellipsoid balloon (read from params file) ----------
 * File format (whitespace separated): cx cy cz  ax ay az  r  [transition]
 * r <= 0 disables the balloon. The 5 cm ellipsoid has axial semi-axis 0.025 m.
 * Velocity inside the ellipsoid is forced to zero every time step (direct forcing IB).
 */
static double B_C[3] = {0.0, 0.0, 0.0};
static double B_A[3] = {0.0, 0.0, 1.0};
static double B_R = 0.0;
static double B_TR = 0.0004;
static int B_LOAD = 0;
static int B_DBG = 0;
static const double B_AX = 0.025;

/* geometry-balloon cases: disable immersed-boundary balloon (wall already in geometry) */
#define ENABLE_IB 0

static void load_balloon(void)
{
    FILE *fp;
    B_LOAD = 1;
#if !ENABLE_IB
    B_R = 0.0;
    return;
#endif
    fp = fopen("C:/Users/MLTZ/Documents/deepseek-harness/default-workspace/tools/wkcase/balloon_params.txt", "r");
    if (fp == NULL) { B_R = 0.0; return; }
    if (fscanf(fp, "%lf %lf %lf %lf %lf %lf %lf", &B_C[0], &B_C[1], &B_C[2],
               &B_A[0], &B_A[1], &B_A[2], &B_R) != 7) B_R = 0.0;
    fclose(fp);
}

static double balloon_mask(double x, double y, double z)
{
    double dx = x - B_C[0], dy = y - B_C[1], dz = z - B_C[2];
    double s = dx * B_A[0] + dy * B_A[1] + dz * B_A[2];
    double lx = dx - s * B_A[0], ly = dy - s * B_A[1], lz = dz - s * B_A[2];
    double l = sqrt(lx * lx + ly * ly + lz * lz);
    double f = sqrt((s * s) / (B_AX * B_AX) + (l * l) / (B_R * B_R));
    double d = (f - 1.0) * B_R;             /* approximate signed distance */
    double m = -d / B_TR;                   /* 1 when deep inside, 0 outside */
    if (m > 1.0) m = 1.0;
    if (m < 0.0) m = 0.0;
    return m;
}

static void zero_velocity_inside(Domain *d)
{
    Thread *th;
    cell_t c;
    if (!B_LOAD) load_balloon();
    if (B_R <= 0.0) return;
    thread_loop_c(th, d)
    {
        if (!FLUID_THREAD_P(th)) continue;
        begin_c_loop(c, th)
        {
            double x[3];
            double m;
            C_CENTROID(x, c, th);
            m = balloon_mask(x[0], x[1], x[2]);
            if (m > 0.0) {
                C_U(c, th) *= (1.0 - m);
                C_V(c, th) *= (1.0 - m);
                C_W(c, th) *= (1.0 - m);
            }
        }
        end_c_loop(c, th)
    }
}

/* ---- complete-occlusion equivalent: momentum sink (porous-like) inside balloon ---- */
#define ENABLE_SRC 1
#if ENABLE_SRC
#ifndef SRC_K
#define SRC_K 1.0e8
#endif
DEFINE_SOURCE(balloon_src_x, c, t, dS, eqn)
{
    double x[3];
    double m;
    if (!B_LOAD) load_balloon();
    if (B_R <= 0.0) { dS[eqn] = 0.0; return 0.0; }
    C_CENTROID(x, c, t);
    m = balloon_mask(x[0], x[1], x[2]);
    if (m <= 0.0) { dS[eqn] = 0.0; return 0.0; }
    dS[eqn] = -SRC_K;
    return -SRC_K * C_U(c, t) * m;
}
DEFINE_SOURCE(balloon_src_y, c, t, dS, eqn)
{
    double x[3];
    double m;
    if (!B_LOAD) load_balloon();
    if (B_R <= 0.0) { dS[eqn] = 0.0; return 0.0; }
    C_CENTROID(x, c, t);
    m = balloon_mask(x[0], x[1], x[2]);
    if (m <= 0.0) { dS[eqn] = 0.0; return 0.0; }
    dS[eqn] = -SRC_K;
    return -SRC_K * C_V(c, t) * m;
}
DEFINE_SOURCE(balloon_src_z, c, t, dS, eqn)
{
    double x[3];
    double m;
    if (!B_LOAD) load_balloon();
    if (B_R <= 0.0) { dS[eqn] = 0.0; return 0.0; }
    C_CENTROID(x, c, t);
    m = balloon_mask(x[0], x[1], x[2]);
    if (m <= 0.0) { dS[eqn] = 0.0; return 0.0; }
    dS[eqn] = -SRC_K;
    return -SRC_K * C_W(c, t) * m;
}
#endif

/* update Pc exactly once per time step (not per inner iteration) */
DEFINE_ADJUST(wk_update, domain)
{
    static real last_time = -1.0;
    real dt = CURRENT_TIMESTEP;
    real t = CURRENT_TIME;
    Thread *th;
    int i;
    WK *wks[3];

    wks[0] = &wk_arch; wks[1] = &wk_renal; wks[2] = &wk_iliac;

    /* immersed-boundary balloon: applied every iteration (steady or transient) */
    zero_velocity_inside(domain);
    if (!B_LOAD) { load_balloon(); }
    if (!B_DBG && B_R > 0.0) {
        B_DBG = 1;
        Message("[IB] balloon center=(%.4f %.4f %.4f) axis=(%.3f %.3f %.3f) r=%.5f m ",
                B_C[0], B_C[1], B_C[2], B_A[0], B_A[1], B_A[2], B_R);
    }

    if (dt <= 0.0) return;
    if (t <= last_time + 1.0e-12) return;
    last_time = t;

    static int wk_initialized = 0;

    for (i = 0; i < 3; i++) {
        WK *w = wks[i];
        w->Q = group_flux(domain, w) / RHO;     /* F_FLUX positive = outward at boundary */
        if (!wk_initialized) {
            /* self-consistent start: Pc = R2*Q from the initial flow field */
            w->Pc = 8000.0 - w->R1 * w->Q;   /* keep p_j = MAP at t=0 */
            if (w->Pc < 0.0) w->Pc = 0.0;
            continue;
        }
        w->Pc = (w->Pc + dt * w->Q / w->C) / (1.0 + dt / (w->R2 * w->C));
        if (w->Pc < 0.0) w->Pc = 0.0;
        if (w->Pc != w->Pc) w->Pc = 0.0;        /* NaN guard */
    }
    wk_initialized = 1;
#if RP_NODE
    Message("[WK] t=%.3f Qa=%.2e Qr=%.2e Qi=%.2e Pca=%.0f Pcr=%.0f Pci=%.0f ",
            t, wk_arch.Q, wk_renal.Q, wk_iliac.Q,
            wk_arch.Pc, wk_renal.Pc, wk_iliac.Pc);
#endif
}

/* outlet pressure P = R1*Q_current + Pc  (Q positive = outflow, F_FLUX sign convention) */
DEFINE_PROFILE(wk_arch_p, thread, position)
{
    face_t f;
    begin_f_loop(f, thread)
        F_PROFILE(f, thread, position) = wk_arch.R1 * wk_arch.Q + wk_arch.Pc + P_VEN;
    end_f_loop(f, thread)
}

DEFINE_PROFILE(wk_renal_p, thread, position)
{
    face_t f;
    begin_f_loop(f, thread)
        F_PROFILE(f, thread, position) = wk_renal.R1 * wk_renal.Q + wk_renal.Pc + P_VEN;
    end_f_loop(f, thread)
}

DEFINE_PROFILE(wk_iliac_p, thread, position)
{
    face_t f;
    begin_f_loop(f, thread)
        F_PROFILE(f, thread, position) = wk_iliac.R1 * wk_iliac.Q + wk_iliac.Pc + P_VEN;
    end_f_loop(f, thread)
}

/* --- steady initialization: pure resistance BC  P = (R1+R2)*Q  ---
 * Used only to obtain a consistent starting field for the transient WK run:
 * all outlets outflow, flow split set by the same R distribution. */
DEFINE_PROFILE(wk_arch_p_st, thread, position)
{
    real Q = thread_flux(thread) / RHO;
    face_t f;
    begin_f_loop(f, thread)
        F_PROFILE(f, thread, position) = (wk_arch.R1 + wk_arch.R2) * Q;
    end_f_loop(f, thread)
}

DEFINE_PROFILE(wk_renal_p_st, thread, position)
{
    real Q = thread_flux(thread) / RHO;
    face_t f;
    begin_f_loop(f, thread)
        F_PROFILE(f, thread, position) = (wk_renal.R1 + wk_renal.R2) * Q;
    end_f_loop(f, thread)
}

DEFINE_PROFILE(wk_iliac_p_st, thread, position)
{
    real Q = thread_flux(thread) / RHO;
    face_t f;
    begin_f_loop(f, thread)
        F_PROFILE(f, thread, position) = (wk_iliac.R1 + wk_iliac.R2) * Q;
    end_f_loop(f, thread)
}

/* Womersley inlet profile (exact harmonic solution for the WK inlet waveform).
 * alpha_1 = R*sqrt(omega/nu) = 35.09; steady part = Poiseuille.
 * u(xi,t) = 2*U0*(1-xi^2) + sum_k Re{ C_k(xi) e^{i k omega t} }, xi=r/R, integral = Q(t). */
#define NPROF 41
#define U0_MEAN 0.048742
#define R_IN 0.02450
#define IN_CX 0.022916
#define IN_CY -0.013608
#define IN_CZ 0.267559
#define IN_NX -0.228471
#define IN_NY -0.212258
#define IN_NZ 0.950130
#define OMEGA 12.566370614
static const real K1_RE[NPROF] = {0.154123, 0.129127, 0.067736, 0.002547, -0.034252, -0.029323, 0.005653, 0.043293, 0.058675, 0.044426, 0.013433, -0.011639, -0.014242, 0.005524, 0.032318, 0.047110, 0.040444, 0.018302, -0.002964, -0.008542, 0.004645, 0.026409, 0.041057, 0.038610, 0.021537, 0.002482, -0.005031, 0.003860, 0.022262, 0.036818, 0.037356, 0.023962, 0.006581, -0.002335, 0.003283, 0.019020, 0.033407, 0.036258, 0.025858, 0.009952, 0.000000};
static const real K1_IM[NPROF] = {-0.322513, -0.270207, -0.141743, -0.005331, 0.071675, 0.061360, -0.011830, -0.090593, -0.122781, -0.092965, -0.028109, 0.024356, 0.029803, -0.011560, -0.067628, -0.098582, -0.084632, -0.038297, 0.006203, 0.017876, -0.009719, -0.055263, -0.085914, -0.080795, -0.045068, -0.005194, 0.010527, -0.008078, -0.046586, -0.077044, -0.078169, -0.050143, -0.013772, 0.004887, -0.006871, -0.039801, -0.069907, -0.075873, -0.054109, -0.020826, 0.000000};
static const real K2_RE[NPROF] = {1.010888, 0.652379, -0.055564, -0.426612, -0.210766, 0.191956, 0.265263, -0.023869, -0.264002, -0.159663, 0.118146, 0.197097, -0.006022, -0.205421, -0.143045, 0.081247, 0.164256, 0.006378, -0.171447, -0.134826, 0.056808, 0.143499, 0.016019, -0.147693, -0.129835, 0.038408, 0.128377, 0.023932, -0.129284, -0.126304, 0.023525, 0.116318, 0.030607, -0.114066, -0.123461, 0.010943, 0.106079, 0.036313, -0.100927, -0.120912, -0.000000};
static const real K2_IM[NPROF] = {1.211919, 0.782115, -0.066614, -0.511450, -0.252680, 0.230129, 0.318014, -0.028616, -0.316503, -0.191415, 0.141641, 0.236293, -0.007219, -0.246272, -0.171491, 0.097404, 0.196921, 0.007646, -0.205542, -0.161638, 0.068105, 0.172036, 0.019205, -0.177064, -0.155654, 0.046046, 0.153907, 0.028691, -0.154994, -0.151422, 0.028203, 0.139449, 0.036693, -0.136749, -0.148013, 0.013120, 0.127174, 0.043534, -0.120998, -0.144958, -0.000000};
static const real K3_RE[NPROF] = {-0.188990, -0.103116, 0.030084, 0.035928, -0.046362, -0.060283, 0.003403, 0.021682, -0.030841, -0.052205, -0.007764, 0.015970, -0.021989, -0.047587, -0.015157, 0.011891, -0.015627, -0.043768, -0.020727, 0.008184, -0.010710, -0.040089, -0.025079, 0.004487, -0.006867, -0.036352, -0.028450, 0.000704, -0.003941, -0.032525, -0.030949, -0.003159, -0.001854, -0.028643, -0.032636, -0.007043, -0.000556, -0.024776, -0.033558, -0.010871, -0.000000};
static const real K3_IM[NPROF] = {0.050598, 0.027607, -0.008054, -0.009619, 0.012412, 0.016139, -0.000911, -0.005805, 0.008257, 0.013977, 0.002079, -0.004276, 0.005887, 0.012740, 0.004058, -0.003184, 0.004184, 0.011718, 0.005549, -0.002191, 0.002867, 0.010733, 0.006714, -0.001201, 0.001839, 0.009733, 0.007617, -0.000188, 0.001055, 0.008708, 0.008286, 0.000846, 0.000496, 0.007668, 0.008738, 0.001886, 0.000149, 0.006633, 0.008984, 0.002910, 0.000000};
static const real K4_RE[NPROF] = {0.013539, 0.004096, -0.007054, -0.002691, 0.003105, -0.001841, -0.004866, 0.000224, 0.001112, -0.003633, -0.002801, 0.001179, -0.000955, -0.003859, -0.000876, 0.000774, -0.002590, -0.002950, 0.000343, -0.000483, -0.003309, -0.001521, 0.000535, -0.001904, -0.003001, -0.000273, -0.000194, -0.002846, -0.001973, 0.000272, -0.001375, -0.002954, -0.000794, -0.000040, -0.002405, -0.002287, -0.000032, -0.000958, -0.002819, -0.001248, -0.000000};
static const real K4_IM[NPROF] = {-0.060488, -0.018298, 0.031513, 0.012023, -0.013872, 0.008224, 0.021740, -0.000999, -0.004968, 0.016232, 0.012516, -0.005267, 0.004266, 0.017241, 0.003912, -0.003460, 0.011570, 0.013181, -0.001532, 0.002156, 0.014784, 0.006797, -0.002389, 0.008506, 0.013408, 0.001218, 0.000867, 0.012713, 0.008812, -0.001213, 0.006145, 0.013198, 0.003547, 0.000179, 0.010746, 0.010219, 0.000143, 0.004279, 0.012594, 0.005574, 0.000000};
static const real K5_RE[NPROF] = {0.002319, 0.000667, -0.000753, 0.000384, 0.000579, -0.000384, 0.000115, 0.000567, -0.000177, -0.000043, 0.000522, -0.000007, -0.000136, 0.000442, 0.000138, -0.000176, 0.000339, 0.000254, -0.000167, 0.000224, 0.000333, -0.000120, 0.000113, 0.000372, -0.000043, 0.000018, 0.000371, 0.000048, -0.000050, 0.000335, 0.000142, -0.000086, 0.000271, 0.000225, -0.000087, 0.000192, 0.000285, -0.000056, 0.000110, 0.000317, 0.000000};
static const real K5_IM[NPROF] = {0.003189, 0.000917, -0.001035, 0.000528, 0.000796, -0.000528, 0.000158, 0.000780, -0.000244, -0.000059, 0.000717, -0.000010, -0.000187, 0.000608, 0.000190, -0.000241, 0.000466, 0.000349, -0.000230, 0.000309, 0.000458, -0.000165, 0.000156, 0.000512, -0.000060, 0.000025, 0.000511, 0.000067, -0.000069, 0.000460, 0.000195, -0.000118, 0.000373, 0.000309, -0.000119, 0.000264, 0.000392, -0.000077, 0.000151, 0.000437, 0.000000};
static const real K6_RE[NPROF] = {-0.021949, -0.004477, 0.005686, -0.006753, -0.001982, 0.002631, -0.005671, -0.001199, 0.001366, -0.005165, -0.000708, 0.000541, -0.004810, -0.000354, -0.000095, -0.004504, -0.000091, -0.000628, -0.004211, 0.000100, -0.001090, -0.003918, 0.000231, -0.001499, -0.003619, 0.000306, -0.001860, -0.003314, 0.000330, -0.002177, -0.003004, 0.000308, -0.002451, -0.002693, 0.000243, -0.002681, -0.002385, 0.000139, -0.002869, -0.002084, -0.000000};
static const real K6_IM[NPROF] = {0.009166, 0.001869, -0.002374, 0.002820, 0.000828, -0.001099, 0.002368, 0.000501, -0.000570, 0.002157, 0.000296, -0.000226, 0.002009, 0.000148, 0.000040, 0.001881, 0.000038, 0.000262, 0.001759, -0.000042, 0.000455, 0.001636, -0.000096, 0.000626, 0.001511, -0.000128, 0.000777, 0.001384, -0.000138, 0.000909, 0.001254, -0.000129, 0.001023, 0.001125, -0.000101, 0.001120, 0.000996, -0.000058, 0.001198, 0.000870, 0.000000};

static real interp_prof(const real *arr, real x)
{
    real f, t;
    int i;
    if (x <= 0.0) return arr[0];
    if (x >= 1.0) return arr[NPROF-1];
    f = x * (NPROF - 1);
    i = (int)f;
    t = f - i;
    if (i >= NPROF - 1) return arr[NPROF-1];
    return arr[i] * (1.0 - t) + arr[i+1] * t;
}

static real womersley_velocity(real xc, real yc, real zc, real t)
{
    real dx = xc - IN_CX, dy = yc - IN_CY, dz = zc - IN_CZ;
    real ax = dx*IN_NX + dy*IN_NY + dz*IN_NZ;
    real rx = dx - ax*IN_NX, ry = dy - ax*IN_NY, rz = dz - ax*IN_NZ;
    real r = sqrt(rx*rx + ry*ry + rz*rz);
    real xi = r / R_IN;
    real v, w = OMEGA * t;
    int k;
    if (xi > 1.0) xi = 1.0;
    v = 2.0 * U0_MEAN * (1.0 - xi*xi);
    v += interp_prof(K1_RE, xi) * cos(w)     - interp_prof(K1_IM, xi) * sin(w);
    v += interp_prof(K2_RE, xi) * cos(2.0*w) - interp_prof(K2_IM, xi) * sin(2.0*w);
    v += interp_prof(K3_RE, xi) * cos(3.0*w) - interp_prof(K3_IM, xi) * sin(3.0*w);
    v += interp_prof(K4_RE, xi) * cos(4.0*w) - interp_prof(K4_IM, xi) * sin(4.0*w);
    v += interp_prof(K5_RE, xi) * cos(5.0*w) - interp_prof(K5_IM, xi) * sin(5.0*w);
    v += interp_prof(K6_RE, xi) * cos(6.0*w) - interp_prof(K6_IM, xi) * sin(6.0*w);
    if (v < 0.0) v = 0.0;   /*           0          */
    return v;
}

/* inlet: no-slip-consistent Womersley profile (was uniform plug).
 * Two-pass normalization: sum(u*dA) is forced to equal Q(t) exactly,
 * so the profile shape accounts for the true (non-circular) inlet face. */
DEFINE_PROFILE(inlet_velocity, thread, position)
{
    real xc[ND_ND];
    real va[ND_ND];
    real s = 0.0, a = 0.0, scale, area;
    real q = inlet_flow(CURRENT_TIME);
    face_t f;
    begin_f_loop(f, thread)
    {
        F_CENTROID(xc, f, thread);
        F_AREA(va, f, thread);
        area = sqrt(va[0]*va[0] + va[1]*va[1] + va[2]*va[2]);
        s += womersley_velocity(xc[0], xc[1], xc[2], CURRENT_TIME) * area;
        a += area;
    }
    end_f_loop(f, thread)
#if RP_NODE
    s = PRF_GRSUM1(s);
#endif
    scale = (s > 1.0e-12) ? q / s : 0.0;
    begin_f_loop(f, thread)
    {
        F_CENTROID(xc, f, thread);
        F_PROFILE(f, thread, position) = scale * womersley_velocity(xc[0], xc[1], xc[2], CURRENT_TIME);
    }
    end_f_loop(f, thread)
}
