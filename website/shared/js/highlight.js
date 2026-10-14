/* Geno syntax highlighter shared by both site versions.
 *
 * Keyword and type lists mirror spec.json; builtin names mirror
 * geno/builtin_manifest.py. Output is a string of escaped HTML with
 * <span class="tok-*"> wrappers, so each site styles tokens itself.
 */
(function (global) {
  "use strict";

  var KEYWORDS = (
    "and assert async await break catch continue do else end ensures example " +
    "export false fn for func if impl import in let match not or ref requires " +
    "return test then throw trait true try type var where while with"
  ).split(" ");
  var KW = new Set(KEYWORDS);
  var CONTRACT = new Set(["requires", "ensures", "example", "assert", "test"]);
  var BLOCK_OPENERS = new Set(["func", "if", "for", "while", "match", "try", "trait", "impl", "test"]);
  var LITERAL = new Set(["true", "false"]);

  var BUILTINS = new Set((
    "abs add all append array_copy array_fill array_from_list array_get array_length array_new " +
    "array_set array_to_list ceil char_code clamp clear_screen cli_args clock_elapsed clock_format " +
    "clock_now clock_parse concat contains csv_parse csv_parse_with_headers datetime_now draw_circle " +
    "draw_line draw_rect draw_text ends_with enumerate env_get env_get_or exec exec_with_input filter " +
    "flat_map float_to_int floor fold format fs_exists fs_list_dir fs_read_text fs_write_text head " +
    "http_fetch http_listen http_post http_request http_respond http_route int_to_float is_none is_some " +
    "join json_parse json_stringify length map map_get map_insert max min parse_float parse_int print " +
    "random_int random_float range regex_match regex_replace replace reverse round set_at sort sort_by " +
    "split sqrt starts_with substring tail to_string to_lower to_upper toml_parse trim unwrap unwrap_or " +
    "vec_new vec_push vec_get vec_set vec_length mutable_map_new mutable_map_set mutable_map_get zip " +
    "string_join string_split string_contains list_map list_filter result_map option_map"
  ).split(" "));

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function wrap(cls, text) {
    return '<span class="tok-' + cls + '">' + esc(text) + "</span>";
  }

  // Ordered token rules for Geno. Each returns [className|null, text].
  var GENO_RE = new RegExp(
    [
      "(\\/\\/\\/[^\\n]*)", // 1 doc comment
      "(\\/\\/[^\\n]*)", // 2 line comment
      "(\\/\\*[\\s\\S]*?\\*\\/)", // 3 block comment
      '(f"(?:[^"\\\\]|\\\\.)*")', // 4 f-string
      '("(?:[^"\\\\]|\\\\.)*")', // 5 string
      "(\\b\\d[\\d_]*(?:\\.\\d+)?(?:e[+-]?\\d+)?\\b)", // 6 number
      "([A-Za-z_][A-Za-z0-9_]*)", // 7 identifier / keyword
      "(->|\\|>|==|!=|<=|>=|&&|\\|\\||[-+*/%<>=|?!])", // 8 operator
      "([()\\[\\]{},:.])", // 9 punctuation
      "(\\s+)", // 10 whitespace
      "(.)", // 11 anything else
    ].join("|"),
    "g"
  );

  function fstring(text) {
    // Highlight {expr} holes inside an f-string.
    var out = '<span class="tok-str">' + esc(text.slice(0, 2));
    var body = text.slice(2, -1);
    var re = /\{[^}]*\}/g;
    var last = 0;
    var m;
    while ((m = re.exec(body))) {
      out += esc(body.slice(last, m.index));
      out += '<span class="tok-interp">{</span>' + highlightGeno(m[0].slice(1, -1)) + '<span class="tok-interp">}</span>';
      last = m.index + m[0].length;
    }
    out += esc(body.slice(last)) + '"</span>';
    return out;
  }

  function highlightGeno(code) {
    var out = "";
    var m;
    var prevWord = "";
    // Fresh regex per call: f-string holes re-enter this function.
    var re = new RegExp(GENO_RE.source, "g");
    while ((m = re.exec(code))) {
      if (m[1]) out += wrap("doc", m[1]);
      else if (m[2]) out += wrap("com", m[2]);
      else if (m[3]) out += wrap("com", m[3]);
      else if (m[4]) out += fstring(m[4]);
      else if (m[5]) out += wrap(prevWord === "end" ? "endlabel" : "str", m[5]);
      else if (m[6]) out += wrap("num", m[6]);
      else if (m[7]) {
        var w = m[7];
        if (prevWord === "end" && (BLOCK_OPENERS.has(w) || w === "func")) out += wrap("end", w);
        else if (w === "end") out += wrap("end", w);
        else if (LITERAL.has(w)) out += wrap("bool", w);
        else if (CONTRACT.has(w)) out += wrap("spec", w);
        else if (KW.has(w)) out += wrap("kw", w);
        else if (prevWord === "func" || prevWord === "fn") out += wrap("fn", w);
        else if (prevWord === "end" && /^[a-z_]/.test(w)) out += wrap("endlabel", w);
        else if (/^[A-Z]/.test(w)) out += wrap("type", w);
        else if (BUILTINS.has(w)) out += wrap("builtin", w);
        else out += esc(w);
        // Treat `end func` as a unit so `end func name` labels the name.
        if (!(prevWord === "end" && BLOCK_OPENERS.has(w))) prevWord = w;
        else prevWord = "end";
        continue;
      } else if (m[8]) out += wrap("op", m[8]);
      else if (m[9]) out += wrap("punct", m[9]);
      else if (m[10]) {
        out += m[10];
        if (m[10].indexOf("\n") !== -1) prevWord = "";
        continue;
      } else out += esc(m[11]);
      prevWord = "";
    }
    return out;
  }

  function highlightShell(code) {
    return code
      .split("\n")
      .map(function (line) {
        var pm = line.match(/^(\s*)(\$|>|PS>)\s(.*)$/);
        if (pm) {
          var rest = esc(pm[3])
            .replace(/(^|\s)(--?[a-z][\w-]*)/g, '$1<span class="tok-flag">$2</span>')
            .replace(/(#.*)$/, '<span class="tok-com">$1</span>');
          rest = rest.replace(/^(\w[\w.-]*)/, '<span class="tok-cmd">$1</span>');
          return pm[1] + '<span class="tok-prompt">' + esc(pm[2]) + "</span> " + rest;
        }
        if (/^\s*#/.test(line)) return wrap("com", line);
        if (/^\s*(PASS|ok)\b/.test(line)) return wrap("pass", line);
        if (/^\s*(FAIL|ERROR|x )/.test(line) || /Error:/.test(line)) return wrap("fail", line);
        if (/^=>/.test(line)) return wrap("result", line);
        return esc(line);
      })
      .join("\n");
  }

  var PY_KW = new Set("def return if else elif for while in not and or is None True False class import from as raise try except with lambda pass break continue yield".split(" "));
  var JS_KW = new Set("function return if else for while const let var new throw try catch class import export from of in typeof instanceof null undefined true false this".split(" "));

  function highlightGeneric(code, kwset, commentRe) {
    var re = new RegExp(
      [commentRe.source, '("(?:[^"\\\\\\n]|\\\\.)*"|\'(?:[^\'\\\\\\n]|\\\\.)*\'|`[^`]*`)', "(\\b\\d+(?:\\.\\d+)?\\b)", "([A-Za-z_$][\\w$]*)", "([\\s\\S])"].join("|"),
      "g"
    );
    var out = "";
    var m;
    var prev = "";
    while ((m = re.exec(code))) {
      if (m[1]) out += wrap("com", m[1]);
      else if (m[2]) out += wrap("str", m[2]);
      else if (m[3]) out += wrap("num", m[3]);
      else if (m[4]) {
        var w = m[4];
        if (kwset.has(w)) out += wrap("kw", w);
        else if (prev === "def" || prev === "function") out += wrap("fn", w);
        else if (/^[A-Z]/.test(w)) out += wrap("type", w);
        else out += esc(w);
        prev = w;
        continue;
      } else out += esc(m[5]);
      if (m[5] && !/\s/.test(m[5])) prev = "";
    }
    return out;
  }

  function highlight(code, lang) {
    lang = (lang || "geno").toLowerCase();
    if (lang === "geno") return highlightGeno(code);
    if (lang === "sh" || lang === "shell" || lang === "bash" || lang === "console") return highlightShell(code);
    if (lang === "py" || lang === "python") return highlightGeneric(code, PY_KW, /(#[^\n]*)/);
    if (lang === "js" || lang === "javascript") return highlightGeneric(code, JS_KW, /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)/);
    if (lang === "toml") return highlightGeneric(code, new Set(["true", "false"]), /(#[^\n]*)/);
    return esc(code);
  }

  function highlightAll(root) {
    (root || document).querySelectorAll("code[data-lang]:not([data-hl])").forEach(function (el) {
      el.innerHTML = highlight(el.textContent, el.getAttribute("data-lang"));
      el.setAttribute("data-hl", "1");
    });
  }

  global.GenoHighlight = { highlight: highlight, highlightAll: highlightAll, escape: esc, keywords: KEYWORDS };
})(typeof window !== "undefined" ? window : globalThis);
