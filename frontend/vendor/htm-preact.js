// node_modules/htm/preact/index.module.js
import { h as r, Component as o, render as t2 } from "preact";
import { h, render, Component } from "preact";

// node_modules/htm/dist/htm.module.js
var n = function(t3, s, r2, e) {
  var u;
  s[0] = 0;
  for (var h2 = 1; h2 < s.length; h2++) {
    var p = s[h2++], a = s[h2] ? (s[0] |= p ? 1 : 2, r2[s[h2++]]) : s[++h2];
    3 === p ? e[0] = a : 4 === p ? e[1] = Object.assign(e[1] || {}, a) : 5 === p ? (e[1] = e[1] || {})[s[++h2]] = a : 6 === p ? e[1][s[++h2]] += a + "" : p ? (u = t3.apply(a, n(t3, a, r2, ["", null])), e.push(u), a[0] ? s[0] |= 2 : (s[h2 - 2] = 0, s[h2] = u)) : e.push(a);
  }
  return e;
};
var t = /* @__PURE__ */ new Map();
function htm_module_default(s) {
  var r2 = t.get(this);
  return r2 || (r2 = /* @__PURE__ */ new Map(), t.set(this, r2)), (r2 = n(this, r2.get(s) || (r2.set(s, r2 = (function(n2) {
    for (var t3, s2, r3 = 1, e = "", u = "", h2 = [0], p = function(n3) {
      1 === r3 && (n3 || (e = e.replace(/^\s*\n\s*|\s*\n\s*$/g, ""))) ? h2.push(0, n3, e) : 3 === r3 && (n3 || e) ? (h2.push(3, n3, e), r3 = 2) : 2 === r3 && "..." === e && n3 ? h2.push(4, n3, 0) : 2 === r3 && e && !n3 ? h2.push(5, 0, true, e) : r3 >= 5 && ((e || !n3 && 5 === r3) && (h2.push(r3, 0, e, s2), r3 = 6), n3 && (h2.push(r3, n3, 0, s2), r3 = 6)), e = "";
    }, a = 0; a < n2.length; a++) {
      a && (1 === r3 && p(), p(a));
      for (var l = 0; l < n2[a].length; l++) t3 = n2[a][l], 1 === r3 ? "<" === t3 ? (p(), h2 = [h2], r3 = 3) : e += t3 : 4 === r3 ? "--" === e && ">" === t3 ? (r3 = 1, e = "") : e = t3 + e[0] : u ? t3 === u ? u = "" : e += t3 : '"' === t3 || "'" === t3 ? u = t3 : ">" === t3 ? (p(), r3 = 1) : r3 && ("=" === t3 ? (r3 = 5, s2 = e, e = "") : "/" === t3 && (r3 < 5 || ">" === n2[a][l + 1]) ? (p(), 3 === r3 && (h2 = h2[0]), r3 = h2, (h2 = h2[0]).push(2, 0, r3), r3 = 0) : " " === t3 || "	" === t3 || "\n" === t3 || "\r" === t3 ? (p(), r3 = 2) : e += t3), 3 === r3 && "!--" === e && (r3 = 4, h2 = h2[0]);
    }
    return p(), h2;
  })(s)), r2), arguments, [])).length > 1 ? r2 : r2[0];
}

// node_modules/htm/preact/index.module.js
var m = htm_module_default.bind(r);
export {
  Component,
  h,
  m as html,
  render
};
