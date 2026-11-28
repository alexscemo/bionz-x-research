#version 460 core
#include <flutter/runtime_effect.glsl>

uniform float uWidth;
uniform float uHeight;
uniform float uTexWidth;
uniform float uTexHeight;
uniform float uIsLog2;
uniform float uSplitMode;
uniform float uSplitPos;

// 18 AdjustParams
uniform float uExposure;
uniform float uContrast;
uniform float uHighlights;
uniform float uShadows;
uniform float uWhites;
uniform float uBlacks;
uniform float uWarmth;
uniform float uTint;
uniform float uVibrance;
uniform float uSaturation;
uniform float uDirCouplers;
uniform float uHalation;
uniform float uGrain;
uniform float uFade;
uniform float uStructure;
uniform float uClarity;
uniform float uSharpen;
uniform float uVignette;

// Film Stock
uniform float uBypassEmulsion;
uniform float uIsMono;
uniform float uM0;
uniform float uM1;
uniform float uM2;
uniform float uM3;
uniform float uM4;
uniform float uM5;
uniform float uM6;
uniform float uM7;
uniform float uM8;
uniform float uGammaR;
uniform float uGammaG;
uniform float uGammaB;
uniform float uToe;
uniform float uShoulder;
uniform float uDm01;
uniform float uDm02;
uniform float uDm10;
uniform float uDm12;
uniform float uDm20;
uniform float uDm21;
uniform float uChromaGain;
uniform float uGrainSize;

// Print Paper
uniform float uPaperContrast;
uniform float uDMinR;
uniform float uDMinG;
uniform float uDMinB;
uniform float uGainR;
uniform float uGainG;
uniform float uGainB;

uniform sampler2D uTexture;

out vec4 fragColor;

vec3 decodeSample(vec2 uv) {
  vec2 clampedUv = clamp(uv, vec2(0.0005), vec2(0.9995));
  vec3 c = texture(uTexture, clampedUv).rgb;
  if (uIsLog2 > 0.5) {
    // 14-stop scene-referred Log2 decoding: [0..1] -> [0..15.9375] linear light
    return max((exp2(c * 8.0) - 1.0) / 16.0, vec3(0.0));
  }
  // Exact inverse sRGB piecewise OETF
  vec3 lo = c / 12.92;
  vec3 hi = pow(max((c + 0.055) / 1.055, vec3(0.00001)), vec3(2.4));
  return mix(lo, hi, step(vec3(0.04045), c));
}

float lumaRec709(vec3 c) {
  return dot(c, vec3(0.2126, 0.7152, 0.0722));
}

float hdCurve(float x, float gamma, float toe, float shoulder) {
  if (x <= 0.0001) return 0.0;
  float boosted = x * (1.0 + shoulder * 0.6);
  float compressed = boosted / (boosted + 0.42 + shoulder * 0.55);
  float mid = clamp(compressed * (1.42 + shoulder * 0.55), 0.0, 1.35);
  float curved = abs(gamma - 1.0) > 0.005 ? pow(max(mid, 0.00001), gamma) : mid;
  return clamp(curved + toe * 0.025 * (1.0 - min(1.0, curved)), 0.0, 1.35);
}

float applyPrintPaper(float v, float contrast, float gain, float dMin) {
  float c = clamp(contrast, 0.55, 1.75);
  float pivot = 0.22;
  float x = v > 0.0001 ? pivot * pow(max(v / pivot, 0.00001), c) : 0.0;
  float soft = (x / (x + 0.85)) * 1.85;
  return clamp(soft * gain + dMin, 0.0, 1.25);
}

vec3 linearToSrgb(vec3 lin) {
  vec3 c = clamp(lin, vec3(0.0), vec3(1.0));
  vec3 lo = c * 12.92;
  vec3 hi = 1.055 * pow(max(c, vec3(0.00001)), vec3(1.0 / 2.4)) - 0.055;
  return mix(lo, hi, step(vec3(0.0031308), c));
}

float hash21(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

vec3 neutralFilmicDisplay(vec3 lin) {
  // Smooth tone-map preserving 14-bit RAW highlight headroom on Original view
  vec3 x = max(lin, vec3(0.0));
  vec3 tone = uIsLog2 > 0.5 ? (x / (x + vec3(0.45)) * 1.45) : x;
  return linearToSrgb(tone);
}

void main() {
  vec2 size = max(vec2(uWidth, uHeight), vec2(1.0));
  vec2 uv = FlutterFragCoord().xy / size;
  vec2 texel = 1.0 / max(vec2(uTexWidth, uTexHeight), vec2(1.0));

  vec3 baseLin = decodeSample(uv);

  bool showOriginal = (uSplitMode > 1.5) || (uSplitMode > 0.5 && uv.x < uSplitPos);
  if (showOriginal) {
    fragColor = vec4(neutralFilmicDisplay(baseLin), 1.0);
    return;
  }

  // Stage 1: EV exposure + linear WB warmth/tint in scene-referred linear space
  float evGain = exp2(uExposure);
  float wbR = evGain * clamp(1.0 + uWarmth * 0.28 + uTint * 0.08, 0.45, 2.2);
  float wbG = evGain * clamp(1.0 - abs(uWarmth) * 0.03 - uTint * 0.16, 0.45, 2.2);
  float wbB = evGain * clamp(1.0 - uWarmth * 0.28 + uTint * 0.08, 0.45, 2.2);
  vec3 wbVec = vec3(wbR, wbG, wbB);

  vec3 lin = baseLin * wbVec;

  // Stage 2: Fast multi-tap red-layer optical halation scatter
  float halStr = uHalation * 1.15;
  if (halStr > 0.01) {
    vec2 r1 = texel * 5.0;
    vec2 r2 = texel * 11.0;
    float acc = 0.0;
    vec3 s0 = decodeSample(uv + vec2(r1.x, 0.0)) * wbVec;
    vec3 s1 = decodeSample(uv - vec2(r1.x, 0.0)) * wbVec;
    vec3 s2 = decodeSample(uv + vec2(0.0, r1.y)) * wbVec;
    vec3 s3 = decodeSample(uv - vec2(0.0, r1.y)) * wbVec;
    vec3 s4 = decodeSample(uv + vec2(r2.x, r2.y)) * wbVec;
    vec3 s5 = decodeSample(uv - vec2(r2.x, r2.y)) * wbVec;
    vec3 s6 = decodeSample(uv + vec2(-r2.x, r2.y)) * wbVec;
    vec3 s7 = decodeSample(uv + vec2(r2.x, -r2.y)) * wbVec;
    acc += max(0.0, dot(s0, vec3(0.35, 0.55, 0.10)) - 0.62);
    acc += max(0.0, dot(s1, vec3(0.35, 0.55, 0.10)) - 0.62);
    acc += max(0.0, dot(s2, vec3(0.35, 0.55, 0.10)) - 0.62);
    acc += max(0.0, dot(s3, vec3(0.35, 0.55, 0.10)) - 0.62);
    acc += max(0.0, dot(s4, vec3(0.35, 0.55, 0.10)) - 0.62) * 0.7;
    acc += max(0.0, dot(s5, vec3(0.35, 0.55, 0.10)) - 0.62) * 0.7;
    acc += max(0.0, dot(s6, vec3(0.35, 0.55, 0.10)) - 0.62) * 0.7;
    acc += max(0.0, dot(s7, vec3(0.35, 0.55, 0.10)) - 0.62) * 0.7;
    float gVal = (acc / 6.8) * halStr;
    lin += vec3(gVal * 1.45, gVal * 0.28, gVal * 0.04);
  }

  // Detail neighborhood sampling (computed in scene-linear before H&D for true crispness & halo control)
  float preLum = max(lumaRec709(lin), 0.0);
  if (abs(uStructure) > 0.005 || abs(uClarity) > 0.005 || uSharpen > 0.005) {
    float lBroad = 0.0;
    if (abs(uClarity) > 0.005) {
      vec2 o = texel * 10.0;
      lBroad += lumaRec709(decodeSample(uv + vec2(o.x, o.y)) * wbVec);
      lBroad += lumaRec709(decodeSample(uv + vec2(-o.x, o.y)) * wbVec);
      lBroad += lumaRec709(decodeSample(uv + vec2(o.x, -o.y)) * wbVec);
      lBroad += lumaRec709(decodeSample(uv + vec2(-o.x, -o.y)) * wbVec);
      lBroad *= 0.25;
      float detailB = preLum - lBroad;
      float midBell = clamp(4.0 * clamp(preLum, 0.0, 1.0) * (1.0 - clamp(preLum, 0.0, 1.0)), 0.0, 1.0);
      float targetB = max(0.0, preLum + detailB * (uClarity * 1.15) * midBell);
      if (preLum > 0.0005) lin *= clamp(targetB / preLum, 0.42, 2.2);
    }
    if (abs(uStructure) > 0.005) {
      vec2 o = texel * 4.0;
      float lMid = 0.0;
      lMid += lumaRec709(decodeSample(uv + vec2(o.x, 0.0)) * wbVec);
      lMid += lumaRec709(decodeSample(uv - vec2(o.x, 0.0)) * wbVec);
      lMid += lumaRec709(decodeSample(uv + vec2(0.0, o.y)) * wbVec);
      lMid += lumaRec709(decodeSample(uv - vec2(0.0, o.y)) * wbVec);
      lMid *= 0.25;
      float curL = max(lumaRec709(lin), 0.0005);
      float posGain = uStructure > 0.0 ? uStructure * 1.55 : uStructure * 0.92;
      float midW = uStructure > 0.0 ? (0.35 + 0.65 * (1.0 - abs(2.0 * clamp(curL, 0.0, 1.0) - 1.0))) : 1.0;
      float targetS = max(0.0, curL + (preLum - lMid) * posGain * midW);
      lin *= clamp(targetS / curL, 0.4, 2.2);
    }
    if (uSharpen > 0.005) {
      vec2 o = texel * 1.25;
      float lFine = 0.0;
      lFine += lumaRec709(decodeSample(uv + vec2(o.x, 0.0)) * wbVec);
      lFine += lumaRec709(decodeSample(uv - vec2(o.x, 0.0)) * wbVec);
      lFine += lumaRec709(decodeSample(uv + vec2(0.0, o.y)) * wbVec);
      lFine += lumaRec709(decodeSample(uv - vec2(0.0, o.y)) * wbVec);
      lFine *= 0.25;
      float curL = max(lumaRec709(lin), 0.0005);
      float detailF = clamp(preLum - lFine, -0.18, 0.18);
      float targetF = max(0.0, curL + detailF * (uSharpen * 0.95));
      lin *= clamp(targetF / curL, 0.55, 1.85);
    }
  }

  // True highlight recovery in linear light BEFORE H&D compression when highlights/whites are pulled down
  if (uIsLog2 > 0.5 && (uHighlights < -0.01 || uWhites < -0.01)) {
    float rawLum = lumaRec709(lin);
    if (rawLum > 0.70) {
      float pull = clamp((-uHighlights * 0.55 - uWhites * 0.40), 0.0, 0.95);
      float knee = 0.70 + (rawLum - 0.70) / (1.0 + pull * 2.4 * max(rawLum - 0.70, 0.0));
      lin *= (knee / rawLum);
    }
  }

  // Stage 3 & 4: Spectral matrix + H&D curve + DIR couplers + Print paper
  float paperC = uPaperContrast * (1.0 + uContrast * 0.45);
  if (uBypassEmulsion < 0.5) {
    float sr = max(0.0, uM0 * lin.r + uM1 * lin.g + uM2 * lin.b);
    float sg = max(0.0, uM3 * lin.r + uM4 * lin.g + uM5 * lin.b);
    float sb = max(0.0, uM6 * lin.r + uM7 * lin.g + uM8 * lin.b);

    float dr = hdCurve(sr, uGammaR, uToe, uShoulder);
    float dg = hdCurve(sg, uGammaG, uToe, uShoulder);
    float db = hdCurve(sb, uGammaB, uToe, uShoulder);

    float dirK = uDirCouplers * 0.65;
    if (dirK > 0.001 && uIsMono < 0.5) {
      float inhR = uDm01 * dg + uDm02 * db;
      float inhG = uDm10 * dr + uDm12 * db;
      float inhB = uDm20 * dr + uDm21 * dg;
      dr = clamp(dr - dirK * (inhR - dr * 0.25), 0.0, 1.35);
      dg = clamp(dg - dirK * (inhG - dg * 0.25), 0.0, 1.35);
      db = clamp(db - dirK * (inhB - db * 0.25), 0.0, 1.35);
    }

    lin.r = applyPrintPaper(dr, paperC, uGainR, uDMinR);
    lin.g = applyPrintPaper(dg, paperC, uGainG, uDMinG);
    lin.b = applyPrintPaper(db, paperC, uGainB, uDMinB);
  } else {
    float neutC = 1.0 + uContrast * 0.45;
    lin.r = applyPrintPaper(clamp(lin.r, 0.0, 1.35), neutC, 1.0, 0.0);
    lin.g = applyPrintPaper(clamp(lin.g, 0.0, 1.35), neutC, 1.0, 0.0);
    lin.b = applyPrintPaper(clamp(lin.b, 0.0, 1.35), neutC, 1.0, 0.0);
  }

  // Stage 5: Highlights / Shadows / Whites / Blacks tone shaping
  float lum = clamp(lumaRec709(lin), 0.0, 1.5);
  if (lum > 0.0005 && (abs(uHighlights) > 0.005 || abs(uShadows) > 0.005 || abs(uWhites) > 0.005 || abs(uBlacks) > 0.005)) {
    float t = clamp(lum, 0.0, 1.0);
    float omt = 1.0 - t;
    float shMask = omt * omt;
    float hiMask = t * t;
    float blMask = shMask * shMask;
    float whMask = hiMask * t;
    float targetLum = max(0.0, lum + uShadows * 0.24 * shMask + uHighlights * 0.22 * hiMask + uBlacks * 0.16 * blMask + uWhites * 0.20 * whMask);
    lin *= clamp(targetLum / lum, 0.28, 2.8);
  }

  // Stage 6: Saturation + Skin-protected Vibrance + Fade lift
  float postLum = lumaRec709(lin);
  float satMul = uIsMono > 0.5 ? 0.0 : clamp(uChromaGain * (1.0 + uSaturation * 0.70), 0.0, 2.2);
  float effSat = satMul;
  if (uIsMono < 0.5 && abs(uVibrance) > 0.005) {
    float maxC = max(lin.r, max(lin.g, lin.b));
    float minC = min(lin.r, min(lin.g, lin.b));
    float satEst = maxC > 0.001 ? clamp((maxC - minC) / maxC, 0.0, 1.0) : 0.0;
    float skinProt = (lin.r > lin.g && lin.g > lin.b) ? 0.55 : 1.0;
    effSat = clamp(satMul * (1.0 + uVibrance * 0.65 * (1.0 - satEst) * skinProt), 0.0, 2.4);
  }
  lin = mix(vec3(postLum), lin, effSat);

  float fadeLift = uFade * 0.085;
  if (fadeLift > 0.0) {
    float comp = 1.0 - fadeLift;
    lin = lin * comp + vec3(fadeLift * (1.0 + uDMinR * 4.0), fadeLift * (1.0 + uDMinG * 4.0), fadeLift * (1.0 + uDMinB * 4.0));
  }

  lin = clamp(lin, vec3(0.0), vec3(1.0));

  // Stage 7: Silver-halide grain & Vignette
  float grainStr = uGrain * max(0.35, uGrainSize) * 0.055;
  if (grainStr > 0.0005) {
    float dLum = clamp(dot(lin, vec3(0.299, 0.587, 0.114)), 0.0, 1.0);
    float dWeight = 4.0 * dLum * (1.0 - dLum) + 0.12;
    vec2 grainCoord = floor(uv * max(vec2(uTexWidth, uTexHeight), vec2(600.0)));
    float n1 = hash21(grainCoord) * 2.0 - 1.0;
    float n2 = hash21(grainCoord + vec2(37.1, 91.7)) * 2.0 - 1.0;
    float nVal = (n1 + n2) * 0.5 * grainStr * dWeight;
    if (uIsMono > 0.5) {
      lin += vec3(nVal);
    } else {
      float cVar = n2 * grainStr * dWeight * 0.25;
      lin += vec3(nVal + cVar, nVal, nVal - cVar);
    }
  }

  float vigStr = uVignette * 0.58;
  if (vigStr > 0.001) {
    vec2 centered = uv * 2.0 - 1.0;
    float r2 = dot(centered, centered);
    float att = clamp(1.0 - vigStr * r2 * 0.65, 0.28, 1.0);
    lin *= att;
  }

  fragColor = vec4(linearToSrgb(lin), 1.0);
}
