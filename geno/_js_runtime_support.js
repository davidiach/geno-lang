"use strict";

// Capture global builtins before module namespaces can shadow them.
const _GENO_STRING = globalThis.String;
const _GENO_NUMBER = globalThis.Number;
const _GENO_MATH = globalThis.Math;
const _GENO_DATE = globalThis.Date;
const _GENO_JSON = globalThis.JSON;
const _GENO_MAP = globalThis.Map;
const _GENO_SET = globalThis.Set;

// =============================================================================
// Safety Limits
// =============================================================================

const _MAX_COLLECTION_SIZE = (
    _GENO_NUMBER.isInteger(globalThis.__GENO_MAX_COLLECTION_SIZE)
    && globalThis.__GENO_MAX_COLLECTION_SIZE >= 0
) ? globalThis.__GENO_MAX_COLLECTION_SIZE : 10_000_000;
const _MAX_INTEGER_BITS = (
    _GENO_NUMBER.isInteger(globalThis.__GENO_MAX_INTEGER_BITS)
    && globalThis.__GENO_MAX_INTEGER_BITS > 0
) ? globalThis.__GENO_MAX_INTEGER_BITS : 33219;
const _MAX_SAFE_JS_INT = _GENO_NUMBER.MAX_SAFE_INTEGER;
const _MIN_SAFE_JS_INT = _GENO_NUMBER.MIN_SAFE_INTEGER;
const _MAX_SAFE_JS_BIGINT = BigInt(_MAX_SAFE_JS_INT);
const _MIN_SAFE_JS_BIGINT = BigInt(_MIN_SAFE_JS_INT);

function _requireInt(funcName, value, argName) {
    if (!_GENO_NUMBER.isInteger(value)) {
        throw new Error(`${funcName} ${argName} must be an integer`);
    }
}

function _requireString(funcName, value, argName = null) {
    if (typeof value !== "string") {
        if (argName !== null) {
            throw new Error(`${funcName} ${argName} must be a string, got ${typeof value}`);
        }
        throw new Error(`${funcName} expects string, got ${typeof value}`);
    }
}

function _stringCodePoints(text) {
    return Array.from(text);
}

function _stringLength(text) {
    return _stringCodePoints(text).length;
}

function _stringCharAt(text, index) {
    const chars = _stringCodePoints(text);
    return (index >= 0 && index < chars.length) ? chars[index] : "";
}

function _stringSubstring(text, start, stop, funcName = "substring") {
    if (funcName === "substring") {
        _requireString(funcName, text);
    } else {
        _requireString(funcName, text, "text");
    }
    _requireInt(funcName, start, "start");
    _requireInt(funcName, stop, "stop");
    const chars = _stringCodePoints(text);
    start = _GENO_MATH.max(0, start);
    stop = _GENO_MATH.min(chars.length, stop);
    return chars.slice(start, stop).join("");
}

function _stringIndexOf(text, sub) {
    const codeUnitIndex = text.indexOf(sub);
    return codeUnitIndex === -1 ? -1 : _stringLength(text.slice(0, codeUnitIndex));
}

function _stringLastIndexOf(text, sub) {
    const codeUnitIndex = text.lastIndexOf(sub);
    return codeUnitIndex === -1 ? -1 : _stringLength(text.slice(0, codeUnitIndex));
}

function _stringPad(text, width, fill_char, left) {
    const padCount = _GENO_MATH.max(0, width - _stringLength(text));
    const padding = fill_char.repeat(padCount);
    return left ? padding + text : text + padding;
}

function _isPythonWhitespace(ch) {
    const cp = ch.codePointAt(0);
    return (
        (cp >= 0x0009 && cp <= 0x000d)
        || (cp >= 0x001c && cp <= 0x0020)
        || cp === 0x0085
        || cp === 0x00a0
        || cp === 0x1680
        || (cp >= 0x2000 && cp <= 0x200a)
        || cp === 0x2028
        || cp === 0x2029
        || cp === 0x202f
        || cp === 0x205f
        || cp === 0x3000
    );
}

function _trimPythonWhitespace(text, trimStart, trimEnd) {
    const chars = _stringCodePoints(text);
    let start = 0;
    let end = chars.length;
    if (trimStart) {
        while (start < end && _isPythonWhitespace(chars[start])) start += 1;
    }
    if (trimEnd) {
        while (end > start && _isPythonWhitespace(chars[end - 1])) end -= 1;
    }
    return chars.slice(start, end).join("");
}

// Node may ship Unicode case mappings newer than the Python versions Geno
// supports. Preserve these code points so JS matches Python str.upper/lower.
const _PYTHON_UNCHANGED_UPPER = new _GENO_SET([
    0x019b, 0x0264, 0x1c8a, 0xa7cd, 0xa7db,
    0x10d70, 0x10d71, 0x10d72, 0x10d73, 0x10d74, 0x10d75, 0x10d76,
    0x10d77, 0x10d78, 0x10d79, 0x10d7a, 0x10d7b, 0x10d7c, 0x10d7d,
    0x10d7e, 0x10d7f, 0x10d80, 0x10d81, 0x10d82, 0x10d83, 0x10d84,
    0x10d85,
]);

const _PYTHON_UNCHANGED_LOWER = new _GENO_SET([
    0x1c89, 0xa7cb, 0xa7cc, 0xa7da, 0xa7dc,
    0x10d50, 0x10d51, 0x10d52, 0x10d53, 0x10d54, 0x10d55, 0x10d56,
    0x10d57, 0x10d58, 0x10d59, 0x10d5a, 0x10d5b, 0x10d5c, 0x10d5d,
    0x10d5e, 0x10d5f, 0x10d60, 0x10d61, 0x10d62, 0x10d63, 0x10d64,
    0x10d65,
]);

function _pythonCase(text, upper) {
    const unchanged = upper ? _PYTHON_UNCHANGED_UPPER : _PYTHON_UNCHANGED_LOWER;
    const chars = _stringCodePoints(text);
    let hasOverride = false;
    for (const ch of chars) {
        const cp = ch.codePointAt(0);
        if (unchanged.has(cp)) {
            hasOverride = true;
            break;
        }
    }

    const nativeResult = upper ? text.toUpperCase() : text.toLowerCase();
    if (!hasOverride) return nativeResult;

    const nativeChars = _stringCodePoints(nativeResult);
    let nativeIndex = 0;
    let result = "";
    for (const ch of chars) {
        const cp = ch.codePointAt(0);
        const nativePiece = upper ? ch.toUpperCase() : ch.toLowerCase();
        const nativePieceSize = _stringLength(nativePiece);
        if (unchanged.has(cp)) {
            result += ch;
        } else {
            result += nativeChars.slice(nativeIndex, nativeIndex + nativePieceSize).join("");
        }
        nativeIndex += nativePieceSize;
    }
    return result;
}

function _checkedPythonCase(funcName, text, upper) {
    const result = _pythonCase(text, upper);
    _checkStringResultSize(funcName, _stringLength(result));
    return result;
}

function _checkCollectionSize(result, kindOverride = null) {
    const stack = [{ value: result, kindOverride }];
    const seen = new _GENO_SET();
    while (stack.length > 0) {
        const item = stack.pop();
        const value = item.value;
        if (typeof value === 'string') {
            _checkCollectionKind(item.kindOverride || "String", _stringLength(value));
            continue;
        }
        if (typeof value === 'number' && _GENO_NUMBER.isInteger(value)) {
            _checkIntegerBits(value);
            continue;
        }
        if (value === null || typeof value !== 'object') continue;
        if (seen.has(value)) continue;
        seen.add(value);

        if (Array.isArray(value)) {
            _checkCollectionKind(item.kindOverride || "List", value.length);
            for (const element of value) stack.push({ value: element, kindOverride: null });
            continue;
        }
        if (value instanceof _GENO_MAP) {
            _checkCollectionKind(item.kindOverride || "Map", value.size);
            for (const [key, mapValue] of value.entries()) {
                stack.push({ value: key, kindOverride: null });
                stack.push({ value: mapValue, kindOverride: null });
            }
            continue;
        }
        if (value instanceof GenoArray) {
            _checkCollectionKind(item.kindOverride || "Array", value.length);
            for (const element of value._elements) stack.push({ value: element, kindOverride: null });
            continue;
        }
        if (value instanceof GenoVec) {
            _checkCollectionKind(item.kindOverride || "Vec", value.length);
            for (const element of value._elements) stack.push({ value: element, kindOverride: null });
            continue;
        }
        if (value instanceof GenoSet) {
            _checkCollectionKind(item.kindOverride || "Set", value.size);
            for (const element of value._data.values()) stack.push({ value: element, kindOverride: null });
            continue;
        }
        if (value instanceof GenoMutableMap) {
            _checkCollectionKind(item.kindOverride || "MutableMap", value._data.size);
            for (const [key, mapValue] of value._data.entries()) {
                stack.push({ value: key, kindOverride: null });
                stack.push({ value: mapValue, kindOverride: null });
            }
            continue;
        }
        if (isConstructor(value)) {
            for (const key of Object.keys(value)) {
                if (key !== "_tag") stack.push({ value: value[key], kindOverride: null });
            }
        }
    }
    return result;
}

function _checkCollectionKind(kind, size) {
    if (size > _MAX_COLLECTION_SIZE) {
        throw new Error(`${kind} size exceeds limit (${size} > ${_MAX_COLLECTION_SIZE})`);
    }
}

function _checkStringResultSize(funcName, size) {
    try {
        _checkCollectionKind("String", size);
    } catch (error) {
        throw new Error(`${funcName}: ${error.message}`);
    }
}

function _splitResultCount(funcName, text, delimiter) {
    if (delimiter === "") throw new Error(`${funcName}: delimiter cannot be empty`);
    let count = 1;
    let index = 0;
    while (true) {
        const next = text.indexOf(delimiter, index);
        if (next === -1) return count;
        count += 1;
        index = next + delimiter.length;
    }
}

function _joinStringsUnderLimit(funcName, parts, separator) {
    const stringParts = parts.map(part => _GENO_STRING(part));
    let size = 0;
    for (const part of stringParts) size += _stringLength(part);
    if (stringParts.length > 1) {
        size += _stringLength(separator) * (stringParts.length - 1);
    }
    _checkStringResultSize(funcName, size);
    return stringParts.join(separator);
}

function _replaceResultSize(text, oldStr, newStr) {
    const textSize = _stringLength(text);
    const newSize = _stringLength(newStr);
    if (oldStr === "") {
        return textSize + (textSize + 1) * newSize;
    }
    let count = 0;
    let index = 0;
    while (true) {
        const next = text.indexOf(oldStr, index);
        if (next === -1) break;
        count += 1;
        index = next + oldStr.length;
    }
    return textSize + count * (newSize - _stringLength(oldStr));
}

function _replacePython(text, oldStr, newStr) {
    if (oldStr === "") {
        const chars = _stringCodePoints(text);
        return chars.length === 0 ? newStr : newStr + chars.join(newStr) + newStr;
    }
    return text.split(oldStr).join(newStr);
}

function _safeIntRangeError(context) {
    return new Error(`${context} exceeds JavaScript safe integer range`);
}

function _integerBitLength(value) {
    if (value === 0) return 0;
    return _GENO_MATH.floor(_GENO_MATH.log2(_GENO_MATH.abs(value))) + 1;
}

function _checkIntegerBits(value) {
    const bits = _integerBitLength(value);
    if (bits > _MAX_INTEGER_BITS) {
        throw new Error(`Integer exceeds maximum size (${bits} bits)`);
    }
    return value;
}

function _requireSafeJsInt(value, context) {
    if (!_GENO_NUMBER.isSafeInteger(value)) {
        throw _safeIntRangeError(context);
    }
    return _checkIntegerBits(value);
}

function _toSafeJsBigInt(value, context) {
    return BigInt(_requireSafeJsInt(value, context));
}

function _fromSafeJsBigInt(value, context) {
    if (value < _MIN_SAFE_JS_BIGINT || value > _MAX_SAFE_JS_BIGINT) {
        throw _safeIntRangeError(context);
    }
    return _checkIntegerBits(_GENO_NUMBER(value));
}

// =============================================================================
// Constructor Base
// =============================================================================

function isConstructor(val) {
    return val !== null && typeof val === 'object' && '_tag' in val;
}

// =============================================================================
// Built-in Types
// =============================================================================

function Some(value) {
    return Object.freeze({ _tag: 'Some', value });
}

const None_ = Object.freeze({ _tag: 'None' });

function Ok(value) {
    return Object.freeze({ _tag: 'Ok', value });
}

function Err(error) {
    return Object.freeze({ _tag: 'Err', error });
}

// JsonValue constructors
function JsonString(value) { return Object.freeze({ _tag: 'JsonString', value }); }
function JsonInt(value) {
    return Object.freeze({
        _tag: 'JsonInt',
        value: _requireSafeJsInt(value, "JsonInt"),
    });
}
function JsonFloat(value) { return Object.freeze({ _tag: 'JsonFloat', value }); }
function JsonBool(value) { return Object.freeze({ _tag: 'JsonBool', value }); }
function JsonNull() { return Object.freeze({ _tag: 'JsonNull' }); }
function JsonArray(items) { return Object.freeze({ _tag: 'JsonArray', items }); }
function JsonObject(entries) { return Object.freeze({ _tag: 'JsonObject', entries }); }

// HttpRequest constructor
function HttpRequest(method, path, query, headers, body) {
    return Object.freeze({ _tag: 'HttpRequest', method, path, query, headers, body });
}

// HttpResponse constructor
function HttpResponse(status, body, headers) {
    return Object.freeze({ _tag: 'HttpResponse', status, body, headers });
}

// ProcessResult constructor
function ProcessResult(exit_code, stdout, stderr) {
    return Object.freeze({ _tag: 'ProcessResult', exit_code, stdout, stderr });
}

// Filesystem metadata constructors
function FileKindFile() { return Object.freeze({ _tag: 'FileKindFile' }); }
function FileKindDirectory() { return Object.freeze({ _tag: 'FileKindDirectory' }); }
function FileKindSymlink() { return Object.freeze({ _tag: 'FileKindSymlink' }); }
function FileKindOther() { return Object.freeze({ _tag: 'FileKindOther' }); }
function FileMetadata(kind, size, modified_ms) {
    return Object.freeze({ _tag: 'FileMetadata', kind, size, modified_ms });
}

// =============================================================================
// Mutable Array
// =============================================================================

class GenoArray {
    constructor(elements) {
        this._elements = elements;
    }
    get length() {
        return this._elements.length;
    }
    *[Symbol.iterator]() { yield* this._elements; }
}

function array_new(size, def_val) {
    if (!_GENO_NUMBER.isInteger(size)) throw new Error("array_new size must be an integer");
    if (size < 0) throw new Error("array_new size must be non-negative, got " + size);
    if (size > _MAX_COLLECTION_SIZE) {
        throw new Error(`Array size exceeds limit (${size} > ${_MAX_COLLECTION_SIZE})`);
    }
    return new GenoArray(Array(size).fill(def_val));
}

function array_from_list(lst) {
    if (!Array.isArray(lst)) throw new Error("array_from_list expects list");
    if (lst.length > _MAX_COLLECTION_SIZE) {
        throw new Error(`Array size exceeds limit (${lst.length} > ${_MAX_COLLECTION_SIZE})`);
    }
    return new GenoArray([...lst]);
}

function array_get(arr, index) {
    if (!(arr instanceof GenoArray)) throw new Error("array_get expects array");
    if (!_GENO_NUMBER.isInteger(index)) throw new Error("array_get index must be an integer");
    if (index < 0 || index >= arr.length) throw new Error("array_get index " + index + " out of bounds (length " + arr.length + ")");
    return arr._elements[index];
}

function array_set(arr, index, value) {
    if (!(arr instanceof GenoArray)) throw new Error("array_set expects array");
    if (!_GENO_NUMBER.isInteger(index)) throw new Error("array_set index must be an integer");
    if (index < 0 || index >= arr.length) throw new Error("array_set index " + index + " out of bounds (length " + arr.length + ")");
    arr._elements[index] = value;
    return null;
}

function array_length(arr) {
    if (!(arr instanceof GenoArray)) throw new Error("array_length expects array");
    return _requireSafeJsInt(arr.length, "array_length result");
}

function array_to_list(arr) {
    if (!(arr instanceof GenoArray)) throw new Error("array_to_list expects array");
    return [...arr._elements];
}

// =============================================================================
// Value Equality (Structural)
// =============================================================================

function _valuesEqual(a, b) {
    if (a === b) return true;
    if (a === null || b === null) return a === b;
    if (typeof a !== typeof b) return false;
    if (a instanceof GenoArray && b instanceof GenoArray) {
        if (a.length !== b.length) return false;
        for (let i = 0; i < a.length; i++) {
            if (!_valuesEqual(a._elements[i], b._elements[i])) return false;
        }
        return true;
    }
    if (Array.isArray(a) && Array.isArray(b)) {
        if (a.length !== b.length) return false;
        for (let i = 0; i < a.length; i++) {
            if (!_valuesEqual(a[i], b[i])) return false;
        }
        return true;
    }
    if (a instanceof _GENO_MAP && b instanceof _GENO_MAP) {
        if (a.size !== b.size) return false;
        for (const [k, v] of a) {
            const other = _mapGetValue(b, k);
            if (other === _MAP_MISSING || !_valuesEqual(v, other)) return false;
        }
        return true;
    }
    if (a instanceof GenoMutableMap && b instanceof GenoMutableMap) {
        if (a._data.size !== b._data.size) return false;
        for (const [k, v] of a._data) {
            const other = _mapGetValue(b._data, k);
            if (other === _MAP_MISSING || !_valuesEqual(v, other)) return false;
        }
        return true;
    }
    if (a instanceof GenoVec && b instanceof GenoVec) {
        if (a._elements.length !== b._elements.length) return false;
        for (let i = 0; i < a._elements.length; i++) {
            if (!_valuesEqual(a._elements[i], b._elements[i])) return false;
        }
        return true;
    }
    if (a instanceof GenoSet && b instanceof GenoSet) {
        if (a._data.size !== b._data.size) return false;
        for (const left of a._data.values()) {
            let matched = false;
            for (const right of b._data.values()) {
                if (_valuesEqual(left, right)) {
                    matched = true;
                    break;
                }
            }
            if (!matched) return false;
        }
        return true;
    }
    if (isConstructor(a) && isConstructor(b)) {
        if (a._tag !== b._tag) return false;
        const keysA = Object.keys(a).filter(k => k !== '_tag');
        const keysB = Object.keys(b).filter(k => k !== '_tag');
        if (keysA.length !== keysB.length) return false;
        for (const key of keysA) {
            if (!_valuesEqual(a[key], b[key])) return false;
        }
        return true;
    }
    return false;
}

const _MAP_MISSING = {};

function _mapFindKey(m, key) {
    if (m.has(key)) return key;
    for (const existingKey of m.keys()) {
        if (_valuesEqual(existingKey, key)) return existingKey;
    }
    return _MAP_MISSING;
}

function _mapGetValue(m, key) {
    const existingKey = _mapFindKey(m, key);
    return existingKey === _MAP_MISSING ? _MAP_MISSING : m.get(existingKey);
}

function _mapSet(m, key, value) {
    const existingKey = _mapFindKey(m, key);
    // Preserve insertion order and the canonical stored key on updates.
    if (existingKey !== _MAP_MISSING) m.set(existingKey, value);
    else m.set(key, value);
    return m;
}

function _mapDelete(m, key) {
    const existingKey = _mapFindKey(m, key);
    if (existingKey !== _MAP_MISSING) m.delete(existingKey);
}

function _mapClone(m) {
    const result = new _GENO_MAP();
    for (const [key, value] of m) _mapSet(result, key, value);
    return result;
}

function _comparePythonStrings(left, right) {
    const leftChars = _stringCodePoints(left);
    const rightChars = _stringCodePoints(right);
    const len = _GENO_MATH.min(leftChars.length, rightChars.length);
    for (let i = 0; i < len; i += 1) {
        const leftCp = leftChars[i].codePointAt(0);
        const rightCp = rightChars[i].codePointAt(0);
        if (leftCp < rightCp) return -1;
        if (leftCp > rightCp) return 1;
    }
    return leftChars.length - rightChars.length;
}

function _genoSortKey(value, seen) {
    if (value !== null && typeof value === 'object') {
        const active = seen || new _GENO_SET();
        if (active.has(value)) return [98, "cycle"];
        seen = new _GENO_SET(active);
        seen.add(value);
    }

    if (value === null || value === undefined) return [0];
    if (typeof value === 'boolean') return [1, value];
    if (typeof value === 'number') return [2, value];
    if (typeof value === 'string') return [4, value];
    if (Array.isArray(value)) {
        return [5, value.map(item => _genoSortKey(item, seen))];
    }
    if (isConstructor(value)) {
        const fields = Object.keys(value).filter(k => k !== '_tag');
        return [
            6,
            value._tag,
            fields.map(k => [k, _genoSortKey(value[k], seen)]),
        ];
    }
    if (value instanceof GenoArray) {
        return [8, value._elements.map(item => _genoSortKey(item, seen))];
    }
    if (value instanceof GenoMutableMap) {
        const entries = [...value._data.entries()].map(([k, v]) => [
            _genoSortKey(k, seen),
            _genoSortKey(v, seen),
        ]);
        entries.sort((a, b) => _compareGenoSortKeys(a[0], b[0]));
        return [9, entries];
    }
    if (value instanceof _GENO_MAP) {
        const entries = [...value.entries()].map(([k, v]) => [
            _genoSortKey(k, seen),
            _genoSortKey(v, seen),
        ]);
        entries.sort((a, b) => _compareGenoSortKeys(a[0], b[0]));
        return [10, entries];
    }
    if (value instanceof GenoSet) {
        const values = [...value._data.values()].map(item => _genoSortKey(item, seen));
        values.sort(_compareGenoSortKeys);
        return [11, values];
    }
    if (value instanceof GenoVec) {
        return [12, value._elements.map(item => _genoSortKey(item, seen))];
    }
    return [99, typeof value, _GENO_STRING(value)];
}

function _compareGenoSortKeys(left, right) {
    if (Array.isArray(left) && Array.isArray(right)) {
        const len = _GENO_MATH.min(left.length, right.length);
        for (let i = 0; i < len; i += 1) {
            const cmp = _compareGenoSortKeys(left[i], right[i]);
            if (cmp !== 0) return cmp;
        }
        return left.length - right.length;
    }
    if (typeof left === 'number' && typeof right === 'number') {
        return left < right ? -1 : left > right ? 1 : 0;
    }
    if (typeof left === 'boolean' && typeof right === 'boolean') {
        return left === right ? 0 : left ? 1 : -1;
    }
    if (typeof left === 'string' && typeof right === 'string') {
        return _comparePythonStrings(left, right);
    }
    const leftText = _GENO_STRING(left);
    const rightText = _GENO_STRING(right);
    return leftText < rightText ? -1 : leftText > rightText ? 1 : 0;
}

function _compareGenoValues(left, right) {
    return _compareGenoSortKeys(_genoSortKey(left), _genoSortKey(right));
}

function _compareOrderedValues(left, right) {
    if (typeof left === 'number' && typeof right === 'number') {
        return left < right ? -1 : left > right ? 1 : 0;
    }
    if (typeof left === 'string' && typeof right === 'string') {
        return _comparePythonStrings(left, right);
    }
    if (typeof left === 'boolean' && typeof right === 'boolean') {
        return left === right ? 0 : left ? 1 : -1;
    }
    if (Array.isArray(left) && Array.isArray(right)) {
        const len = _GENO_MATH.min(left.length, right.length);
        for (let i = 0; i < len; i += 1) {
            const cmp = _compareOrderedValues(left[i], right[i]);
            if (cmp !== 0) return cmp;
        }
        return left.length - right.length;
    }
    throw new Error("Ordered comparison is not supported for these values");
}

// =============================================================================
// Value Formatting
// =============================================================================

const _GENO_FORMATTER = Symbol("geno.formatter");

function _withGenoFormatter(value, formatter) {
    Object.defineProperty(value, _GENO_FORMATTER, {
        value: formatter,
        enumerable: false,
    });
    return value;
}

function _formatValue(value) {
    if (value === null || value === undefined) return '()';
    if (typeof value === 'boolean') return _GENO_STRING(value);
    if (typeof value === 'string') return _reprString(value);
    if (typeof value === 'number') return _GENO_STRING(value);
    if (value instanceof GenoArray) return 'Array([' + value._elements.map(_formatValue).join(', ') + '])';
    if (Array.isArray(value)) return '[' + value.map(_formatValue).join(', ') + ']';
    if (value instanceof _GENO_MAP) {
        const entries = [...value.entries()].map(([k, v]) => _formatValue(k) + ': ' + _formatValue(v));
        return '{' + entries.join(', ') + '}';
    }
    if (value instanceof GenoMutableMap) {
        const entries = [...value._data.entries()].map(([k, v]) => _formatValue(k) + ': ' + _formatValue(v));
        return 'MutableMap({' + entries.join(', ') + '})';
    }
    if (value instanceof GenoSet) {
        const values = [...value._data.values()].sort(_compareGenoValues).map(_formatValue);
        return 'Set({' + values.join(', ') + '})';
    }
    if (value instanceof GenoVec) return 'Vec([' + value._elements.map(_formatValue).join(', ') + '])';
    if (isConstructor(value)) {
        const formatter = value[_GENO_FORMATTER];
        if (typeof formatter === "function") return formatter(value, undefined, false);
        const fields = Object.keys(value).filter(k => k !== '_tag');
        if (fields.length === 0) return value._tag;
        const fieldStrs = fields.map(k => `${k}: ${_formatValue(value[k])}`);
        return value._tag + '(' + fieldStrs.join(', ') + ')';
    }
    return _GENO_STRING(value);
}

function _reprString(value) {
    return _GENO_JSON.stringify(value);
}

function _stringifyValue(value, seen, topLevel = true) {
    const active = seen || new _GENO_SET();
    if (value !== null && typeof value === 'object') {
        if (active.has(value)) {
            if (value instanceof GenoArray) return "Array([...])";
            if (value instanceof GenoMutableMap) return "MutableMap({...})";
            if (value instanceof GenoVec) return "Vec([...])";
            if (value instanceof GenoSet) return "Set({...})";
            if (value instanceof _GENO_MAP) return "{...}";
            if (Array.isArray(value)) return "[...]";
            return isConstructor(value) ? value._tag + "(...)" : _GENO_STRING(value);
        }
        seen = new _GENO_SET(active);
        seen.add(value);
    }

    if (value === null || value === undefined) return "()";
    if (typeof value === 'string') return topLevel ? value : _reprString(value);
    if (typeof value === 'boolean') return _GENO_STRING(value);
    if (typeof value === 'number') return _GENO_STRING(value);
    if (value instanceof GenoArray) {
        return "Array([" + value._elements.map(item => _stringifyValue(item, seen, false)).join(", ") + "])";
    }
    if (value instanceof GenoMutableMap) {
        const entries = [...value._data.entries()].map(([k, v]) => (
            _stringifyValue(k, seen, false) + ": " + _stringifyValue(v, seen, false)
        ));
        return "MutableMap({" + entries.join(", ") + "})";
    }
    if (value instanceof GenoVec) {
        return "Vec([" + value._elements.map(item => _stringifyValue(item, seen, false)).join(", ") + "])";
    }
    if (value instanceof GenoSet) {
        const values = [...value._data.values()]
            .sort(_compareGenoValues)
            .map(item => _stringifyValue(item, seen, false));
        return "Set({" + values.join(", ") + "})";
    }
    if (Array.isArray(value)) {
        return "[" + value.map(item => _stringifyValue(item, seen, false)).join(", ") + "]";
    }
    if (value instanceof _GENO_MAP) {
        const entries = [...value.entries()].map(([k, v]) => (
            _stringifyValue(k, seen, false) + ": " + _stringifyValue(v, seen, false)
        ));
        return "{" + entries.join(", ") + "}";
    }
    if (isConstructor(value)) {
        const formatter = value[_GENO_FORMATTER];
        if (typeof formatter === "function") return formatter(value, seen, topLevel);
        const fields = Object.keys(value).filter(k => k !== "_tag");
        if (fields.length === 0) return value._tag;
        const fieldStrs = fields.map(k => `${k}: ${_stringifyValue(value[k], seen, false)}`);
        return value._tag + "(" + fieldStrs.join(", ") + ")";
    }
    return _GENO_STRING(value);
}

function _formatFloat(value) {
    if (Object.is(value, -0)) return "-0.0";
    const absValue = _GENO_MATH.abs(value);
    let rendered = (absValue >= 1e16 || (absValue > 0 && absValue < 1e-4))
        ? value.toExponential()
        : _GENO_STRING(value);
    rendered = rendered.replace(/e([+-])(\d)$/, (_match, sign, digit) => `e${sign}0${digit}`);
    if (rendered.includes("e")) return rendered;
    return _GENO_NUMBER.isInteger(value) ? (rendered + ".0") : rendered;
}

// =============================================================================
// Deep Copy
// =============================================================================

function _deepCopy(val) {
    if (val === null || val === undefined) return val;
    if (typeof val !== 'object') return val;
    if (val instanceof GenoArray) return val;
    if (Array.isArray(val)) return val.map(_deepCopy);
    if (val instanceof _GENO_MAP) {
        const result = new _GENO_MAP();
        for (const [k, v] of val) result.set(_deepCopy(k), _deepCopy(v));
        return result;
    }
    if ('_tag' in val) {
        const copy = {};
        for (const key of Object.keys(val)) {
            copy[key] = _deepCopy(val[key]);
        }
        return Object.isFrozen(val) ? Object.freeze(copy) : copy;
    }
    return val;
}

// =============================================================================
// Field Access
// =============================================================================

function get_field(value, fieldName) {
    if (
        value !== null &&
        typeof value === 'object' &&
        Object.prototype.hasOwnProperty.call(value, fieldName)
    ) {
        return value[fieldName];
    }
    throw new Error("'" + ((value && value._tag) || typeof value) + "' has no field '" + fieldName + "'");
}

// =============================================================================
// Safe Operations
// =============================================================================

function _divZero() { throw new Error("Division by zero"); }

function _safe_div(a, b) {
    if (b === 0) throw new Error("Division by zero");
    if (_GENO_NUMBER.isInteger(a) && _GENO_NUMBER.isInteger(b)) {
        const quotient = _toSafeJsBigInt(a, "Integer division operand")
            / _toSafeJsBigInt(b, "Integer division operand");
        return _fromSafeJsBigInt(quotient, "Integer division result");
    }
    return a / b;
}

function _safe_mod(a, b) {
    if (b === 0) throw new Error("Division by zero");
    if (_GENO_NUMBER.isInteger(a) && _GENO_NUMBER.isInteger(b)) {
        const remainder = _toSafeJsBigInt(a, "Modulo operand")
            % _toSafeJsBigInt(b, "Modulo operand");
        return _fromSafeJsBigInt(remainder, "Modulo result");
    }
    return a % b;
}

function _float_div(a, b) {
    if (b === 0) throw new Error("Division by zero");
    return a / b;
}

function _float_power(a, b) {
    if (b < 0 && a === 0) _divZero();
    return _checkPowerNumberResult(a ** b);
}

function _safe_add(a, b) {
    if (_GENO_NUMBER.isInteger(a) && _GENO_NUMBER.isInteger(b)) {
        const result = _toSafeJsBigInt(a, "Addition operand")
            + _toSafeJsBigInt(b, "Addition operand");
        return _fromSafeJsBigInt(result, "Addition result");
    }
    const result = a + b;
    if (typeof result === 'string' || Array.isArray(result)) _checkCollectionSize(result);
    return result;
}

function _safe_sub(a, b) {
    if (_GENO_NUMBER.isInteger(a) && _GENO_NUMBER.isInteger(b)) {
        const result = _toSafeJsBigInt(a, "Subtraction operand")
            - _toSafeJsBigInt(b, "Subtraction operand");
        return _fromSafeJsBigInt(result, "Subtraction result");
    }
    return a - b;
}

function _safe_mul(a, b) {
    if (_GENO_NUMBER.isInteger(a) && _GENO_NUMBER.isInteger(b)) {
        const result = _toSafeJsBigInt(a, "Multiplication operand")
            * _toSafeJsBigInt(b, "Multiplication operand");
        return _fromSafeJsBigInt(result, "Multiplication result");
    }
    if (typeof a === 'string' && typeof b === 'number') {
        if (a.length * _GENO_MATH.max(b, 0) > _MAX_COLLECTION_SIZE) {
            throw new Error('String size exceeds limit');
        }
    }
    if (Array.isArray(a) && typeof b === 'number') {
        if (a.length * _GENO_MATH.max(b, 0) > _MAX_COLLECTION_SIZE) {
            throw new Error('List size exceeds limit');
        }
    }
    return a * b;
}

function _checkPowerNumberResult(result) {
    if (typeof result === 'number' && _GENO_NUMBER.isNaN(result)) {
        throw new Error("Exponentiation result is not a real number");
    }
    if (
        typeof result === 'number'
        && !_GENO_NUMBER.isFinite(result)
    ) {
        throw new Error("Exponentiation result too large");
    }
    return result;
}

function _safe_power(a, b) {
    if (typeof b === 'number' && b < 0) {
        if (a === 0) _divZero();
        return _checkPowerNumberResult(a ** b);
    }
    if (typeof b === 'number' && b > _MAX_INTEGER_BITS) throw new Error("Exponent too large (" + b + " bits)");
    if (_GENO_NUMBER.isInteger(a) && _GENO_NUMBER.isInteger(b)) {
        const result = _toSafeJsBigInt(a, "Exponentiation operand")
            ** _toSafeJsBigInt(b, "Exponentiation operand");
        return _fromSafeJsBigInt(result, "Exponentiation result");
    }
    return _checkPowerNumberResult(a ** b);
}

function _safe_lshift(a, b) {
    if (typeof b === 'number' && b < 0) throw new Error("Negative shift count");
    if (typeof b === 'number' && b > _MAX_INTEGER_BITS) throw new Error("Left shift amount too large (" + b + " bits)");
    const result = _toSafeJsBigInt(a, "Left shift operand")
        << _toSafeJsBigInt(b, "Left shift amount");
    return _fromSafeJsBigInt(result, "Left shift result");
}

function _safe_rshift(a, b) {
    if (typeof b === 'number' && b < 0) throw new Error("Negative shift count");
    if (typeof b === 'number' && b > _MAX_INTEGER_BITS) throw new Error("Right shift amount too large (" + b + " bits)");
    const result = _toSafeJsBigInt(a, "Right shift operand")
        >> _toSafeJsBigInt(b, "Right shift amount");
    return _fromSafeJsBigInt(result, "Right shift result");
}

function _safe_bitor(a, b) {
    const result = _toSafeJsBigInt(a, "Bitwise or operand")
        | _toSafeJsBigInt(b, "Bitwise or operand");
    return _fromSafeJsBigInt(result, "Bitwise or result");
}

function _safe_bitand(a, b) {
    const result = _toSafeJsBigInt(a, "Bitwise and operand")
        & _toSafeJsBigInt(b, "Bitwise and operand");
    return _fromSafeJsBigInt(result, "Bitwise and result");
}

function _safe_bitxor(a, b) {
    const result = _toSafeJsBigInt(a, "Bitwise xor operand")
        ^ _toSafeJsBigInt(b, "Bitwise xor operand");
    return _fromSafeJsBigInt(result, "Bitwise xor result");
}

function _safe_bitnot(a) {
    const result = ~_toSafeJsBigInt(a, "Bitwise not operand");
    return _fromSafeJsBigInt(result, "Bitwise not result");
}

function _safe_neg(a) {
    if (_GENO_NUMBER.isInteger(a)) {
        return _fromSafeJsBigInt(-_toSafeJsBigInt(a, "Negation operand"), "Negation result");
    }
    return -a;
}

function _safe_index(target, index) {
    if (Array.isArray(target)) {
        if (!_GENO_NUMBER.isInteger(index)) throw new Error("List index must be integer");
        if (index < 0) index += target.length;
        if (index < 0 || index >= target.length) throw new Error("Index " + index + " out of bounds");
        return target[index];
    }
    if (target instanceof GenoArray) {
        if (!_GENO_NUMBER.isInteger(index)) throw new Error("Array index must be integer");
        if (index < 0) index += target.length;
        if (index < 0 || index >= target.length) throw new Error("Index " + index + " out of bounds");
        return target._elements[index];
    }
    if (typeof target === 'string') {
        if (!_GENO_NUMBER.isInteger(index)) throw new Error("String index must be integer");
        const chars = _stringCodePoints(target);
        if (index < 0) index += chars.length;
        if (index < 0 || index >= chars.length) throw new Error("Index " + index + " out of bounds");
        return chars[index];
    }
    if (target instanceof _GENO_MAP) {
        const value = _mapGetValue(target, index);
        if (value === _MAP_MISSING) throw new Error("Key not found: " + index);
        return value;
    }
    throw new Error("Cannot index into " + typeof target);
}

function _safe_index_set(target, index, value) {
    _checkCollectionSize(index);
    _checkCollectionSize(value);
    if (target instanceof GenoArray) {
        if (!_GENO_NUMBER.isInteger(index)) throw new Error("Array index must be integer");
        if (index < 0 || index >= target.length) throw new Error("Index " + index + " out of bounds for assignment");
        target._elements[index] = value;
        return;
    }
    if (target instanceof GenoVec) {
        if (!_GENO_NUMBER.isInteger(index)) throw new Error("Vec index must be integer");
        if (index < 0 || index >= target.length) throw new Error("Index " + index + " out of bounds for assignment");
        target._elements[index] = value;
        return;
    }
    if (target instanceof GenoMutableMap) {
        if (_mapFindKey(target._data, index) === _MAP_MISSING) {
            _checkCollectionKind("MutableMap", target._data.size + 1);
        }
        _mapSet(target._data, index, value);
        return;
    }
    if (Array.isArray(target)) {
        if (!_GENO_NUMBER.isInteger(index)) throw new Error("List index must be integer");
        if (index < 0) index += target.length;
        if (index < 0 || index >= target.length) throw new Error("Index " + index + " out of bounds for assignment");
        target[index] = value;
        return;
    }
    if (target instanceof _GENO_MAP) {
        if (_mapFindKey(target, index) === _MAP_MISSING) {
            _checkCollectionKind("Map", target.size + 1);
        }
        _mapSet(target, index, value);
        return;
    }
    throw new Error("Cannot assign to index of " + typeof target);
}

// =============================================================================
// List Operations
// =============================================================================

function length(lst) {
    if (typeof lst === 'string') return _requireSafeJsInt(_stringLength(lst), "length result");
    return _requireSafeJsInt(lst.length, "length result");
}

function head(lst) {
    if (lst.length === 0) throw new Error("head of empty list");
    return lst[0];
}

function tail(lst) {
    if (lst.length === 0) throw new Error("tail of empty list");
    return lst.slice(1);
}

function append(lst, item) {
    _checkCollectionKind("List", lst.length + 1);
    const result = [...lst, item];
    return result;
}

function concat(lst1, lst2) {
    _checkCollectionKind("List", lst1.length + lst2.length);
    const result = [...lst1, ...lst2];
    return result;
}

function set_at(lst, index, value) {
    if (index < 0 || index >= lst.length) throw new Error("set_at index out of range");
    const result = [...lst];
    result[index] = value;
    return result;
}

function filter_(lst, pred) {
    return lst.filter(pred);
}

function map_(lst, func) {
    const result = lst.map(func);
    _checkCollectionSize(result);
    return result;
}

function fold(lst, init, func) {
    let acc = init;
    for (const x of lst) {
        acc = func(acc, x);
        if (typeof acc === 'string' || Array.isArray(acc)) _checkCollectionSize(acc);
    }
    return acc;
}

function contains(lst, item) {
    for (const x of lst) {
        if (_valuesEqual(x, item)) return true;
    }
    return false;
}

function reverse(lst) {
    return [...lst].reverse();
}

function bit_or(a, b) {
    return _safe_bitor(a, b);
}

function range_(...args) {
    let start, end, step;
    if (args.length === 2) {
        [start, end] = args;
        step = 1;
    } else if (args.length === 3) {
        [start, end, step] = args;
    } else {
        throw new Error(`range expects 2 or 3 arguments, got ${args.length}`);
    }
    _requireInt("range", start, "start");
    _requireInt("range", end, "end");
    _requireInt("range", step, "step");
    if (step === 0) throw new Error("range step cannot be zero");
    if (step > 0 && start >= end) return [];
    if (step < 0 && start <= end) return [];
    const size = _GENO_MATH.abs(_GENO_MATH.ceil((end - start) / step));
    if (size > _MAX_COLLECTION_SIZE) {
        throw new Error(`List size exceeds limit (${size} > ${_MAX_COLLECTION_SIZE})`);
    }
    const result = [];
    if (step > 0) {
        for (let i = start; i < end; i += step) result.push(_requireSafeJsInt(i, "range result"));
    } else {
        for (let i = start; i > end; i += step) result.push(_requireSafeJsInt(i, "range result"));
    }
    return result;
}

function take_while(lst, pred) {
    const result = [];
    for (const x of lst) {
        if (pred(x)) result.push(x);
        else break;
    }
    return result;
}

function all_(lst, pred) {
    return lst.every(pred);
}

function sort(lst, cmp) {
    const indexed = lst.map((x, i) => [x, i]);
    indexed.sort((a, b) => {
        const c = cmp(a[0], b[0]);
        return c !== 0 ? c : a[1] - b[1];
    });
    return indexed.map(t => t[0]);
}

function sort_by(lst, key_fn) {
    const keyed = lst.map((x, i) => [key_fn(x), i, x]);
    keyed.sort((a, b) => {
        const cmp = _compareGenoValues(a[0], b[0]);
        return cmp !== 0 ? cmp : a[1] - b[1];
    });
    return keyed.map(t => t[2]);
}

function slice_(lst, start, stop) {
    start = _GENO_MATH.max(0, start);
    stop = _GENO_MATH.min(lst.length, stop);
    return lst.slice(start, stop);
}

// =============================================================================
// String Operations
// =============================================================================

function split(s, sep) {
    _checkCollectionKind("List", _splitResultCount("split", s, sep));
    const result = s.split(sep);
    return result;
}

function join(lst, sep) {
    return _joinStringsUnderLimit("join", lst, sep);
}

function trim(s) { return _trimPythonWhitespace(s, true, true); }
function to_lower(s) { return _checkedPythonCase("to_lower", s, false); }
function to_upper(s) { return _checkedPythonCase("to_upper", s, true); }
function replace(text, old_str, new_str) {
    _checkStringResultSize("replace", _replaceResultSize(text, old_str, new_str));
    return _replacePython(text, old_str, new_str);
}
function ends_with(text, suffix) { return text.endsWith(suffix); }
function starts_with(s, prefix) { return s.startsWith(prefix); }

function to_chars(s) {
    const result = [...s];
    _checkCollectionSize(result);
    return result;
}

function sort_strings(values) {
    if (values.length > 100000) throw new Error("sort_strings: list too large");
    if (!values.every(v => typeof v === 'string')) throw new Error("sort_strings expects a list of strings");
    return [...values].sort(_comparePythonStrings);
}

function split_once(s, sep) {
    if (sep === "") throw new Error("split_once: delimiter cannot be empty");
    const idx = s.indexOf(sep);
    if (idx === -1) return None_;
    return Some([s.slice(0, idx), s.slice(idx + sep.length)]);
}

function substring(s, start, stop) {
    return _stringSubstring(s, start, stop);
}

function format(template, values) {
    const parts = template.split("{}");
    if (parts.length - 1 !== values.length) {
        throw new Error("format: expected " + (parts.length - 1) + " values, got " + values.length);
    }
    const valueParts = values.map(value => _GENO_STRING(value));
    let size = 0;
    for (const part of parts) size += _stringLength(part);
    for (const part of valueParts) size += _stringLength(part);
    _checkStringResultSize("format", size);
    let result = parts[0];
    for (let i = 0; i < valueParts.length; i++) {
        result += valueParts[i] + parts[i + 1];
    }
    return result;
}

// =============================================================================
// Char Codes
// =============================================================================

function char_code(s) {
    if (typeof s !== 'string') throw new Error("char_code expects string");
    if (s.length === 0) throw new Error("char_code: empty string");
    return _requireSafeJsInt(s.codePointAt(0), "char_code result");
}

function from_char_code(n) {
    if (!_GENO_NUMBER.isInteger(n)) throw new Error("from_char_code expects integer");
    if (n < 0 || n > 0x10FFFF) throw new Error("from_char_code: code point " + n + " out of range");
    return _GENO_STRING.fromCodePoint(n);
}

// =============================================================================
// Math
// =============================================================================

function divide(a, b) {
    return _safe_div(a, b);
}

function sqrt(x) {
    if (x < 0) throw new Error("sqrt of negative number");
    return _GENO_MATH.sqrt(x);
}

function floor(x) { return _requireSafeJsInt(_GENO_MATH.floor(x), "floor result"); }
function ceil(x) { return _requireSafeJsInt(_GENO_MATH.ceil(x), "ceil result"); }
function _roundNearest(x, context) {
    const lower = _GENO_MATH.floor(x);
    const result = x - lower >= 0.5 ? lower + 1 : lower;
    return _requireSafeJsInt(result, context);
}

function round_(x) { return _roundNearest(x, "round result"); }

function max_(a, b) { return a >= b ? a : b; }
function abs_(x) { return _GENO_MATH.abs(x); }
function square(x) { return _safe_mul(x, x); }
function add(a, b) { return _safe_add(a, b); }
function subtract(a, b) { return _safe_sub(a, b); }
function multiply(a, b) { return _safe_mul(a, b); }

// =============================================================================
// Conversions
// =============================================================================

function _isDecimalIntString(s) { return /^-?[0-9]+$/.test(s.trim()); }

function parse_int(s) {
    if (typeof s !== 'string' || s.length === 0) return None_;
    if (_stringLength(s) > 1000) {
        throw new Error("parse_int: input string too long (max 1000 characters)");
    }
    const trimmed = s.trim();
    if (!_isDecimalIntString(s)) return None_;
    let parsed;
    try {
        parsed = BigInt(trimmed);
    } catch (_error) {
        return None_;
    }
    if (parsed < _MIN_SAFE_JS_BIGINT || parsed > _MAX_SAFE_JS_BIGINT) return None_;
    return Some(_fromSafeJsBigInt(parsed, "parse_int"));
}

function parse_float(s) {
    if (typeof s !== 'string' || s.length === 0) return None_;
    if (_stringLength(s) > 1000) {
        throw new Error("parse_float: input string too long (max 1000 characters)");
    }
    const trimmed = s.trim();
    if (!/^-?(\d+\.?\d*|\.\d+)$/.test(trimmed)) return None_;
    const n = parseFloat(trimmed);
    if (!_GENO_NUMBER.isFinite(n)) return None_;
    return Some(n);
}

function to_string(x) {
    const result = _stringifyValue(x);
    _checkStringResultSize("to_string", _stringLength(result));
    return result;
}

function float_to_int(x) { return _requireSafeJsInt(_GENO_MATH.trunc(x), "float_to_int result"); }
function int_to_float(x) { return x; }

// =============================================================================
// Option Operations
// =============================================================================

class _PropagateReturn {
    constructor(value) { this.value = value; }
}
class _GenoThrow {
    constructor(value) { this.value = value; }
}
class _GenoContractViolation extends Error {
    constructor(message) {
        super(message);
        this.name = "_GenoContractViolation";
    }
}
function _propagate(val) {
    if (isConstructor(val) && val._tag === 'Some') return val.value;
    if (_valuesEqual(val, None_) || (isConstructor(val) && val._tag === 'None')) throw new _PropagateReturn(val);
    if (isConstructor(val) && val._tag === 'Ok') return val.value;
    if (isConstructor(val) && val._tag === 'Err') throw new _PropagateReturn(val);
    throw new Error("? operator requires Option or Result, got " + typeof val);
}

function is_some(opt) { return isConstructor(opt) && opt._tag === 'Some'; }
function is_none(opt) { return _valuesEqual(opt, None_) || (isConstructor(opt) && opt._tag === 'None'); }

function unwrap(opt) {
    if (isConstructor(opt) && opt._tag === 'Some') return opt.value;
    throw new Error("unwrap called on None");
}

function unwrap_or(opt, def) {
    if (isConstructor(opt) && opt._tag === 'Some') return opt.value;
    return def;
}

// =============================================================================
// Map Operations
// =============================================================================

function map_insert(m, key, value) {
    if (_mapFindKey(m, key) === _MAP_MISSING) {
        _checkCollectionKind("Map", m.size + 1);
    }
    const result = _mapClone(m);
    _mapSet(result, key, value);
    return result;
}

function map_get(m, key) {
    const value = _mapGetValue(m, key);
    return value === _MAP_MISSING ? None_ : Some(value);
}

// =============================================================================
// IO
// =============================================================================

function print_(value) {
    _requireCap("print", "print");
    console.log(typeof value === 'string' ? value : _formatValue(value));
}

// =============================================================================
// Clock / Random
// =============================================================================

function clock_now() {
    _requireCap("clock", "clock_now");
    return _requireSafeJsInt(_GENO_MATH.floor(_GENO_DATE.now() / 1000), "clock_now result");
}
function random_int(lo, hi) {
    _requireCap("random", "random_int");
    const min = _requireSafeJsInt(lo, "random_int lower bound");
    const max = _requireSafeJsInt(hi, "random_int upper bound");
    if (min > max) throw new Error("random_int: lower bound must be <= upper bound");
    const span = _fromSafeJsBigInt(
        _toSafeJsBigInt(max, "random_int upper bound")
        - _toSafeJsBigInt(min, "random_int lower bound")
        + 1n,
        "random_int span",
    );
    return _requireSafeJsInt(
        _GENO_MATH.floor(_GENO_MATH.random() * span) + min,
        "random_int result",
    );
}
function random_float() {
    _requireCap("random", "random_float");
    return _GENO_MATH.random();
}

// =============================================================================
// Predicates
// =============================================================================

function is_sorted(lst) {
    for (let i = 0; i < lst.length - 1; i++) {
        if (lst[i] > lst[i + 1]) return false;
    }
    return true;
}

function is_positive(x) { return x > 0; }

function is_numeric_string(s) {
    if (typeof s !== 'string') return false;
    return _isDecimalIntString(s);
}

function is_permutation(lst1, lst2) {
    if (lst1.length > 100000 || lst2.length > 100000) {
        throw new Error("is_permutation: list too large (max 100,000 elements)");
    }
    if (lst1.length !== lst2.length) return false;

    // Canonical structural sort keys turn the previous O(n^2) unmatched scan
    // into O(n log n), while the final equality check preserves Geno semantics.
    const left = [...lst1].sort(_compareGenoValues);
    const right = [...lst2].sort(_compareGenoValues);
    for (let i = 0; i < left.length; i++) {
        if (!_valuesEqual(left[i], right[i])) return false;
    }
    return true;
}

// =============================================================================
// Array Helpers
// =============================================================================

function array_fill(arr, value) {
    if (!(arr instanceof GenoArray)) throw new Error("array_fill expects array");
    arr._elements.fill(value);
    return null;
}

function array_copy(arr) {
    if (!(arr instanceof GenoArray)) throw new Error("array_copy expects array");
    return new GenoArray([...arr._elements]);
}

// =============================================================================
// Graphics (App Mode)
// =============================================================================

function clear_screen(color) {
    if (typeof _geno_ctx !== 'undefined') {
        _geno_ctx.fillStyle = color;
        _geno_ctx.fillRect(0, 0, _geno_canvas.width, _geno_canvas.height);
    }
    return null;
}

function draw_rect(x, y, w, h, color) {
    if (typeof _geno_ctx !== 'undefined') {
        _geno_ctx.fillStyle = color;
        _geno_ctx.fillRect(x, y, w, h);
    }
    return null;
}

function draw_rect_outline(x, y, w, h, color) {
    if (typeof _geno_ctx !== 'undefined') {
        _geno_ctx.strokeStyle = color;
        _geno_ctx.strokeRect(x, y, w, h);
    }
    return null;
}

function draw_circle(x, y, radius, color) {
    if (typeof _geno_ctx !== 'undefined') {
        _geno_ctx.fillStyle = color;
        _geno_ctx.beginPath();
        _geno_ctx.arc(x, y, radius, 0, 2 * _GENO_MATH.PI);
        _geno_ctx.fill();
    }
    return null;
}

function draw_line(x1, y1, x2, y2, color) {
    if (typeof _geno_ctx !== 'undefined') {
        _geno_ctx.strokeStyle = color;
        _geno_ctx.beginPath();
        _geno_ctx.moveTo(x1, y1);
        _geno_ctx.lineTo(x2, y2);
        _geno_ctx.stroke();
    }
    return null;
}

function draw_text(text, x, y, size, color) {
    if (typeof _geno_ctx !== 'undefined') {
        _geno_ctx.fillStyle = color;
        _geno_ctx.font = size + 'px monospace';
        _geno_ctx.fillText(text, x, y);
    }
    return null;
}

function screen_width() {
    const width = typeof _geno_canvas !== 'undefined' ? _geno_canvas.width : 800;
    return _requireSafeJsInt(width, "screen_width result");
}

function screen_height() {
    const height = typeof _geno_canvas !== 'undefined' ? _geno_canvas.height : 600;
    return _requireSafeJsInt(height, "screen_height result");
}

// =============================================================================
// Input (App Mode)
// =============================================================================

const _geno_keys_down = new _GENO_SET();
const _geno_keys_pressed = new _GENO_SET();

if (typeof document !== 'undefined') {
    document.addEventListener('keydown', function(e) {
        _geno_keys_down.add(e.key);
        _geno_keys_pressed.add(e.key);
        e.preventDefault();
    });
    document.addEventListener('keyup', function(e) {
        _geno_keys_down.delete(e.key);
    });
}

function is_key_down(key) {
    return _geno_keys_down.has(key);
}

function is_key_pressed(key) {
    return _geno_keys_pressed.has(key);
}

function _geno_clear_pressed_keys() {
    _geno_keys_pressed.clear();
}

// =============================================================================
// Mouse Input (App Mode)
// =============================================================================

let _geno_mouse_x = 0;
let _geno_mouse_y = 0;
let _geno_mouse_down = false;
let _geno_mouse_clicked = false;

if (typeof document !== 'undefined' && typeof _geno_canvas !== 'undefined') {
    _geno_canvas.addEventListener('mousemove', function(e) {
        const rect = _geno_canvas.getBoundingClientRect();
        _geno_mouse_x = _GENO_MATH.floor(e.clientX - rect.left);
        _geno_mouse_y = _GENO_MATH.floor(e.clientY - rect.top);
    });
    _geno_canvas.addEventListener('mousedown', function(e) {
        _geno_mouse_down = true;
        _geno_mouse_clicked = true;
    });
    _geno_canvas.addEventListener('mouseup', function(e) {
        _geno_mouse_down = false;
    });
}

function mouse_x() {
    return _requireSafeJsInt(_geno_mouse_x, "mouse_x result");
}

function mouse_y() {
    return _requireSafeJsInt(_geno_mouse_y, "mouse_y result");
}

function is_mouse_down() {
    return _geno_mouse_down;
}

function is_mouse_clicked() {
    return _geno_mouse_clicked;
}

function _geno_clear_mouse_clicked() {
    _geno_mouse_clicked = false;
}

// =============================================================================
// Text Input (App Mode)
// =============================================================================

let _geno_text_buffer = "";

if (typeof document !== 'undefined') {
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Backspace') {
            _geno_text_buffer = _geno_text_buffer.slice(0, -1);
        } else if (e.key.length === 1 && _geno_text_buffer.length < 10000) {
            _geno_text_buffer += e.key;
        }
    });
}

function get_text_input() {
    return _geno_text_buffer;
}

function clear_text_input() {
    _geno_text_buffer = "";
    return null;
}

// =============================================================================
// MutableMap
// =============================================================================

class GenoMutableMap {
    constructor() {
        this._data = new _GENO_MAP();
    }
}

function mutable_map_new() {
    return new GenoMutableMap();
}

function mutable_map_set(m, key, value) {
    if (!(m instanceof GenoMutableMap)) throw new Error("mutable_map_set expects MutableMap");
    if (_mapFindKey(m._data, key) === _MAP_MISSING) {
        _checkCollectionKind("MutableMap", m._data.size + 1);
    }
    _mapSet(m._data, key, value);
    return null;
}

function mutable_map_get(m, key) {
    if (!(m instanceof GenoMutableMap)) throw new Error("mutable_map_get expects MutableMap");
    const value = _mapGetValue(m._data, key);
    return value === _MAP_MISSING ? None_ : Some(value);
}

function mutable_map_contains(m, key) {
    if (!(m instanceof GenoMutableMap)) throw new Error("mutable_map_contains expects MutableMap");
    return _mapFindKey(m._data, key) !== _MAP_MISSING;
}

function mutable_map_delete(m, key) {
    if (!(m instanceof GenoMutableMap)) throw new Error("mutable_map_delete expects MutableMap");
    _mapDelete(m._data, key);
    return null;
}

function mutable_map_size(m) {
    if (!(m instanceof GenoMutableMap)) throw new Error("mutable_map_size expects MutableMap");
    return _requireSafeJsInt(m._data.size, "mutable_map_size result");
}

function mutable_map_keys(m) {
    if (!(m instanceof GenoMutableMap)) throw new Error("mutable_map_keys expects MutableMap");
    return [...m._data.keys()];
}

// =============================================================================
// Vec (Growable List)
// =============================================================================

class GenoVec {
    constructor(elements) {
        this._elements = elements || [];
    }
    get length() { return this._elements.length; }
}

function vec_new() {
    return new GenoVec();
}

function vec_push(v, item) {
    if (!(v instanceof GenoVec)) throw new Error("vec_push expects Vec");
    _checkCollectionKind("Vec", v.length + 1);
    v._elements.push(item);
    return null;
}

function vec_get(v, index) {
    if (!(v instanceof GenoVec)) throw new Error("vec_get expects Vec");
    if (!_GENO_NUMBER.isInteger(index)) throw new Error("vec_get index must be integer");
    if (index < 0 || index >= v.length) throw new Error("vec_get index " + index + " out of bounds (length " + v.length + ")");
    return v._elements[index];
}

function vec_set(v, index, value) {
    if (!(v instanceof GenoVec)) throw new Error("vec_set expects Vec");
    if (!_GENO_NUMBER.isInteger(index)) throw new Error("vec_set index must be integer");
    if (index < 0 || index >= v.length) throw new Error("vec_set index " + index + " out of bounds (length " + v.length + ")");
    v._elements[index] = value;
    return null;
}

function vec_length(v) {
    if (!(v instanceof GenoVec)) throw new Error("vec_length expects Vec");
    return _requireSafeJsInt(v.length, "vec_length result");
}

function vec_pop(v) {
    if (!(v instanceof GenoVec)) throw new Error("vec_pop expects Vec");
    if (v.length === 0) return None_;
    return Some(v._elements.pop());
}

function vec_to_list(v) {
    if (!(v instanceof GenoVec)) throw new Error("vec_to_list expects Vec");
    _checkCollectionKind("List", v.length);
    return [...v._elements];
}

function vec_from_list(lst) {
    if (!Array.isArray(lst)) throw new Error("vec_from_list expects list");
    _checkCollectionKind("Vec", lst.length);
    return new GenoVec([...lst]);
}

// =============================================================================
// Set
// =============================================================================

class GenoSet {
    constructor(data) {
        // _data is a Map keyed by _GENO_JSON.stringify(value) -> original value
        this._data = data || new _GENO_MAP();
    }
    get size() { return this._data.size; }
}

function _setKey(item) {
    return _GENO_JSON.stringify(item);
}

function set_new() {
    return new GenoSet();
}

function set_from_list(lst) {
    if (!Array.isArray(lst)) throw new Error("set_from_list expects list");
    const data = new _GENO_MAP();
    for (const item of lst) {
        const key = _setKey(item);
        if (!data.has(key)) {
            _checkCollectionKind("Set", data.size + 1);
        }
        data.set(key, item);
    }
    return new GenoSet(data);
}

function set_add(s, item) {
    if (!(s instanceof GenoSet)) throw new Error("set_add expects Set");
    const key = _setKey(item);
    if (!s._data.has(key)) {
        _checkCollectionKind("Set", s._data.size + 1);
    }
    s._data.set(key, item);
    return null;
}

function set_remove(s, item) {
    if (!(s instanceof GenoSet)) throw new Error("set_remove expects Set");
    s._data.delete(_setKey(item));
    return null;
}

function set_contains(s, item) {
    if (!(s instanceof GenoSet)) throw new Error("set_contains expects Set");
    return s._data.has(_setKey(item));
}

function set_size(s) {
    if (!(s instanceof GenoSet)) throw new Error("set_size expects Set");
    return _requireSafeJsInt(s.size, "set_size result");
}

function set_to_list(s) {
    if (!(s instanceof GenoSet)) throw new Error("set_to_list expects Set");
    _checkCollectionKind("List", s._data.size);
    return [...s._data.values()].sort(_compareGenoValues);
}

function set_union(a, b) {
    if (!(a instanceof GenoSet) || !(b instanceof GenoSet)) throw new Error("set_union expects two Sets");
    let expected = a._data.size;
    for (const key of b._data.keys()) {
        if (!a._data.has(key)) expected += 1;
    }
    _checkCollectionKind("Set", expected);
    const result = new _GENO_MAP(a._data);
    for (const [k, v] of b._data) result.set(k, v);
    return new GenoSet(result);
}

function set_intersection(a, b) {
    if (!(a instanceof GenoSet) || !(b instanceof GenoSet)) throw new Error("set_intersection expects two Sets");
    const result = new _GENO_MAP();
    for (const [k, v] of a._data) {
        if (b._data.has(k)) result.set(k, v);
    }
    return new GenoSet(result);
}

// =============================================================================
// Clock Builtins
// =============================================================================

// Directives supported by the narrow clock/datetime format/parse contract.
// Mirrors Python's `_CLOCK_DIRECTIVES` in builtins.py and _runtime_support.py.
const _CLOCK_DIRECTIVES = new Set(['Y', 'm', 'd', 'H', 'M', 'S', '%']);

function _validateClockFmt(funcName, fmt) {
    if (typeof fmt !== 'string') {
        throw new Error(funcName + ": fmt must be a string");
    }
    const n = fmt.length;
    let i = 0;
    while (i < n) {
        if (fmt[i] === '%') {
            if (i + 1 >= n) {
                throw new Error(funcName + ": trailing '%' with no directive in format string");
            }
            const nxt = fmt[i + 1];
            if (!_CLOCK_DIRECTIVES.has(nxt)) {
                throw new Error(
                    funcName + ": unsupported format directive '%" + nxt +
                    "' (supported: %Y %m %d %H %M %S %%)"
                );
            }
            i += 2;
        } else {
            i++;
        }
    }
}

function _isValidUtcParts(y, mo, d, h, mi, s) {
    if (y < 1 || y > 9999) return false;
    if (mo < 1 || mo > 12) return false;
    if (h < 0 || h > 23) return false;
    if (mi < 0 || mi > 59) return false;
    if (s < 0 || s > 59) return false;
    const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
    const mdays = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    return d >= 1 && d <= mdays[mo - 1];
}

function clock_format(timestamp, fmt) {
    _requireCap("clock", "clock_format");
    if (typeof timestamp !== 'number') {
        throw new Error("clock_format: timestamp must be a number");
    }
    if (timestamp < 0) {
        throw new Error("clock_format: negative timestamps (pre-1970) are not supported");
    }
    _validateClockFmt("clock_format", fmt);
    const d = new _GENO_DATE(timestamp * 1000);
    const pad = (n, w) => _GENO_STRING(n).padStart(w || 2, '0');
    // Handle %% (literal percent) via sentinel to avoid double-replacement
    const sentinel = '\x00PCT\x00';
    const result = fmt
        .split('%%').join(sentinel)
        .split('%Y').join(pad(d.getUTCFullYear(), 4))
        .split('%m').join(pad(d.getUTCMonth() + 1))
        .split('%d').join(pad(d.getUTCDate()))
        .split('%H').join(pad(d.getUTCHours()))
        .split('%M').join(pad(d.getUTCMinutes()))
        .split('%S').join(pad(d.getUTCSeconds()))
        .split(sentinel).join('%');
    _checkStringResultSize("clock_format", _stringLength(result));
    return result;
}

function clock_parse(text, fmt) {
    _requireCap("clock", "clock_parse");
    if (typeof text !== 'string') {
        throw new Error("clock_parse: text must be a string");
    }
    _validateClockFmt("clock_parse", fmt);
    // Build regex: replace directives with capture groups, escape the rest
    const directives = {'%Y':'(?<Y>\\d{4})','%m':'(?<m>\\d{2})','%d':'(?<d>\\d{2})',
                        '%H':'(?<H>\\d{2})','%M':'(?<M>\\d{2})','%S':'(?<S>\\d{2})'};
    let pattern = '';
    for (let i = 0; i < fmt.length; i++) {
        const two = fmt.slice(i, i + 2);
        if (two === '%%') { pattern += '%'; i++; }
        else if (directives[two]) { pattern += directives[two]; i++; }
        else { pattern += fmt[i].replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
    }
    try {
        const m = text.match(new RegExp('^' + pattern + '$'));
        if (!m) return None_;
        const g = m.groups || {};
        const y = parseInt(g.Y || '1970', 10);
        const mo = parseInt(g.m || '1', 10);
        const d = parseInt(g.d || '1', 10);
        const h = parseInt(g.H || '0', 10);
        const mi = parseInt(g.M || '0', 10);
        const s = parseInt(g.S || '0', 10);
        if (!_isValidUtcParts(y, mo, d, h, mi, s)) return None_;
        const dt = new _GENO_DATE(_GENO_DATE.UTC(
            y, mo - 1, d, h, mi, s
        ));
        if (y >= 1 && y <= 99) dt.setUTCFullYear(y);
        return Some(dt.getTime() / 1000);
    } catch (e) {
        return None_;
    }
}

function clock_elapsed(start, end_time) {
    _requireCap("clock", "clock_elapsed");
    return end_time - start;
}

// =============================================================================
// Capability Parsing (for standalone compiled output)
// =============================================================================

function _capsFromList(values) {
    const caps = new _GENO_SET();
    if (!Array.isArray(values)) return caps;
    values.forEach(value => {
        if (typeof value === "string" && value.length > 0) caps.add(value);
    });
    return caps;
}

const _GENO_CAPS = (function() {
    try {
        if (
            typeof globalThis !== "undefined"
            && Array.isArray(globalThis.__GENO_CAPS)
        ) {
            return _capsFromList(globalThis.__GENO_CAPS);
        }
        if (typeof process === "undefined" || !process.argv) return new _GENO_SET();
        const argv = process.argv;
        const caps = new _GENO_SET();
        for (let i = 2; i < argv.length; i++) {
            if (argv[i] === "--cap" && i + 1 < argv.length) {
                argv[i + 1].split(",").forEach(c => caps.add(c));
                i++;
            }
        }
        return caps;
    } catch (e) {
        return new _GENO_SET();
    }
})();

function _requireCap(capName, builtinName) {
    if (!_GENO_CAPS.has(capName)) {
        throw new Error("Capability denied: '" + builtinName + "' requires '--cap " + capName + "'");
    }
}

function _minimalProcessEnv() {
    if (typeof process === "undefined" || !process.env) return {};
    const env = {};
    for (const key of [
        "PATH",
        "Path",
        "SystemRoot",
        "SYSTEMROOT",
        "WINDIR",
        "windir",
        "COMSPEC",
        "ComSpec",
        "PATHEXT",
    ]) {
        if (process.env[key] !== undefined) {
            env[key] = process.env[key];
        }
    }
    return env;
}

function _processEnv() {
    if (_GENO_CAPS.has("env")) return process.env;
    return _minimalProcessEnv();
}

// =============================================================================
// File I/O Builtins (capability-gated: --cap fs, Node.js only)
// =============================================================================

function fs_read_text(path) {
    _requireCap("fs", "fs_read_text");
    if (typeof require === "undefined") {
        throw new Error("fs_read_text is not available in browser context");
    }
    const fs = require("fs");
    const result = fs.readFileSync(path, "utf-8").replace(/\r\n?/g, "\n");
    _checkStringResultSize("fs_read_text", _stringLength(result));
    return result;
}

function fs_write_text(path, content) {
    _requireCap("fs", "fs_write_text");
    if (typeof require === "undefined") {
        throw new Error("fs_write_text is not available in browser context");
    }
    const fs = require("fs");
    fs.writeFileSync(path, content, "utf-8");
    return null;
}

function fs_list_dir(path) {
    _requireCap("fs", "fs_list_dir");
    if (typeof require === "undefined") {
        return { _tag: "Err", error: "fs_list_dir is not available in browser context" };
    }
    const fs = require("fs");
    let entries;
    try {
        entries = fs.readdirSync(path).sort(_comparePythonStrings);
    } catch (e) {
        return { _tag: "Err", error: e.message };
    }
    return _checkCollectionSize(Ok(entries));
}

function fs_exists(path) {
    _requireCap("fs", "fs_exists");
    if (typeof require === "undefined") {
        throw new Error("fs_exists is not available in browser context");
    }
    const fs = require("fs");
    return fs.existsSync(path);
}

function _fileMetadataFromStats(stats, fnName) {
    let kind;
    if (stats.isFile()) kind = FileKindFile();
    else if (stats.isDirectory()) kind = FileKindDirectory();
    else if (stats.isSymbolicLink()) kind = FileKindSymlink();
    else kind = FileKindOther();

    const size = _fromSafeJsBigInt(stats.size, fnName + " size");
    const divisor = 1000000n;
    let modifiedMs = stats.mtimeNs / divisor;
    if (stats.mtimeNs < 0n && stats.mtimeNs % divisor !== 0n) modifiedMs -= 1n;
    return FileMetadata(
        kind,
        size,
        _fromSafeJsBigInt(modifiedMs, fnName + " modified_ms")
    );
}

function fs_metadata(path) {
    _requireCap("fs", "fs_metadata");
    if (typeof require === "undefined") {
        return Err("fs_metadata is not available in browser context");
    }
    const fs = require("fs");
    let stats;
    try {
        stats = fs.statSync(path, { bigint: true });
    } catch (e) {
        return Err(e.message);
    }
    return _checkCollectionSize(Ok(_fileMetadataFromStats(stats, "fs_metadata")));
}

function fs_symlink_metadata(path) {
    _requireCap("fs", "fs_symlink_metadata");
    if (typeof require === "undefined") {
        return Err("fs_symlink_metadata is not available in browser context");
    }
    const fs = require("fs");
    let stats;
    try {
        stats = fs.lstatSync(path, { bigint: true });
    } catch (e) {
        return Err(e.message);
    }
    return _checkCollectionSize(
        Ok(_fileMetadataFromStats(stats, "fs_symlink_metadata"))
    );
}

function fs_canonicalize(path) {
    _requireCap("fs", "fs_canonicalize");
    if (typeof require === "undefined") {
        return Err("fs_canonicalize is not available in browser context");
    }
    const fs = require("fs");
    let canonical;
    try {
        canonical = fs.realpathSync(path).replace(/\\/g, "/");
    } catch (e) {
        return Err(e.message);
    }
    _checkStringResultSize("fs_canonicalize", _stringLength(canonical));
    return _checkCollectionSize(Ok(canonical));
}

// =============================================================================
// HTTP Builtins (capability-gated: --cap http, Node.js/browser networking)
// =============================================================================

// Synchronous HTTP via child_process: spawns a sub-node process that runs
// an async fetch and writes the result to stdout.  This keeps the main
// compiled output synchronous while using Node's built-in fetch (18+). In
// Node, this internal bridge also requires the process capability so that an
// http-only grant never hides child-process execution.
function _validateHttpScheme(url, fnName) {
    const match = /^[A-Za-z][A-Za-z0-9+.-]*:/.exec(String(url));
    const scheme = match ? match[0].slice(0, -1).toLowerCase() : "";
    if (scheme !== "http" && scheme !== "https") {
        throw new Error(
            `${fnName}: scheme '${scheme}' is not allowed, only http and https`
        );
    }
}

function _parseBrowserResponseHeaders(rawHeaders) {
    const headers = {};
    if (!rawHeaders) return headers;
    rawHeaders.trim().split(/[\r\n]+/).forEach(line => {
        const idx = line.indexOf(":");
        if (idx <= 0) return;
        const key = line.slice(0, idx).trim();
        const value = line.slice(idx + 1).trim();
        if (key) headers[key] = value;
    });
    return headers;
}

function _syncBrowserFetch(method, url, headers, body) {
    if (typeof XMLHttpRequest === "undefined") {
        throw new Error("HTTP builtins are not available in this JavaScript context");
    }
    const xhr = new XMLHttpRequest();
    xhr.open(method, url, false);
    for (const [key, value] of Object.entries(headers || {})) {
        xhr.setRequestHeader(key, value);
    }
    try {
        xhr.send(body === null ? null : body);
        return {
            ok: true,
            status: xhr.status,
            body: xhr.responseText,
            headers: _parseBrowserResponseHeaders(xhr.getAllResponseHeaders()),
        };
    } catch (e) {
        return {ok: false, status: 0, body: "", headers: {}, error: e.message};
    }
}

function _syncFetch(method, url, headers, body, builtinName) {
    if (typeof require === "undefined") {
        return _syncBrowserFetch(method, url, headers, body);
    }
    _requireCap("process", builtinName);
    const cp = require("child_process");
    const script = `
        (async () => {
            const opts = { method: ${_GENO_JSON.stringify(method)} };
            const hdrs = ${_GENO_JSON.stringify(headers || {})};
            if (Object.keys(hdrs).length) opts.headers = hdrs;
            if (${_GENO_JSON.stringify(body)} !== null) opts.body = ${_GENO_JSON.stringify(body)};
            try {
                const r = await fetch(${_GENO_JSON.stringify(url)}, opts);
                const t = await r.text();
                process.stdout.write(_GENO_JSON.stringify({ok: true, status: r.status, body: t}));
            } catch (e) {
                process.stdout.write(_GENO_JSON.stringify({ok: false, error: e.message}));
            }
        })();
    `;
    const result = cp.execFileSync(process.execPath, ["-e", script], {
        encoding: "utf-8",
        timeout: 30000,
    });
    return _GENO_JSON.parse(result);
}

function http_fetch(url) {
    _requireCap("http", "http_fetch");
    _validateHttpScheme(url, "http_fetch");
    const r = _syncFetch("GET", url, null, null, "http_fetch");
    if (!r.ok) throw new Error(r.error);
    _checkStringResultSize("http_fetch", _stringLength(r.body));
    return r.body;
}

function http_post(url, body) {
    _requireCap("http", "http_post");
    _validateHttpScheme(url, "http_post");
    const r = _syncFetch("POST", url, {"Content-Type": "text/plain"}, body, "http_post");
    if (!r.ok) throw new Error(r.error);
    _checkStringResultSize("http_post", _stringLength(r.body));
    return r.body;
}

function http_request(method, url, headers, body) {
    _requireCap("http", "http_request");
    try {
        _validateHttpScheme(url, "http_request");
    } catch (e) {
        return { _tag: "Err", error: e.message };
    }
    const hMap = {};
    if (headers && typeof headers === "object") {
        if (headers._tag === "Some" && headers.value) {
            for (const [k, v] of Object.entries(headers.value)) hMap[k] = v;
        } else if (!headers._tag) {
            for (const [k, v] of Object.entries(headers)) hMap[k] = v;
        }
    }
    const bodyStr = (body && body._tag === "Some") ? body.value : (typeof body === "string" ? body : null);
    const r = _syncFetch(method, url, hMap, bodyStr, "http_request");
    if (!r.ok) return { _tag: "Err", error: r.error };
    const responseHeaders = r.headers || {};
    const responseHeaderEntries = Object.entries(responseHeaders);
    _checkStringResultSize("http_request", _stringLength(r.body));
    _checkCollectionKind("Map", responseHeaderEntries.length);
    for (const [key, value] of responseHeaderEntries) {
        _checkStringResultSize("http_request", _stringLength(key));
        _checkStringResultSize("http_request", _stringLength(String(value)));
    }
    return {
        _tag: "Ok",
        value: { status: r.status, body: r.body, headers: responseHeaders }
    };
}

// =============================================================================
// Regex Builtins
// =============================================================================

const _MAX_REGEX_PATTERN_LEN = 1000;
const _MAX_REGEX_TEXT_LEN = 10000;
const _MAX_REGEX_REPEAT = _MAX_REGEX_TEXT_LEN;
const _MAX_REGEX_GROUP_DEPTH = 128;
const _BACKREF_RE = /\\[1-9]|\(\?P=[A-Za-z_][A-Za-z0-9_]*\)|\\k<[A-Za-z_][A-Za-z0-9_]*>/;

const _PORTABLE_REGEX_LITERAL_ESCAPES = new _GENO_SET("\\.^$|?*+()[]{}");
const _PORTABLE_REGEX_CLASS_ESCAPES = new _GENO_SET("\\^-]");

function _isAsciiRegexDigit(character) {
    return character >= "0" && character <= "9";
}

function _regexGroupDepthExceedsLimit(pattern) {
    let depth = 0;
    let inClass = false;
    let i = 0;
    while (i < pattern.length) {
        const character = pattern[i];
        if (character === "\\") {
            i += 2;
            continue;
        }
        if (character === "[" && !inClass) {
            inClass = true;
        } else if (character === "]" && inClass) {
            inClass = false;
        } else if (!inClass && character === "(") {
            depth++;
            if (depth > _MAX_REGEX_GROUP_DEPTH) return true;
        } else if (!inClass && character === ")" && depth > 0) {
            depth--;
        }
        i++;
    }
    return false;
}

function _portableRegexQuantifierEnd(pattern, start) {
    let i = start + 1;
    const lowerStart = i;
    while (i < pattern.length && _isAsciiRegexDigit(pattern[i])) i++;
    if (i === lowerStart) return null;
    const lowerText = pattern.slice(lowerStart, i);
    const lowerSignificant = lowerText.replace(/^0+/, "");
    if (lowerSignificant.length > 5) return null;
    const lower = lowerSignificant === "" ? 0 : Number(lowerSignificant);
    let upper = lower;
    if (i < pattern.length && pattern[i] === ",") {
        i++;
        const upperStart = i;
        while (i < pattern.length && _isAsciiRegexDigit(pattern[i])) i++;
        const upperText = pattern.slice(upperStart, i);
        const upperSignificant = upperText.replace(/^0+/, "");
        if (upperSignificant.length > 5) return null;
        upper = upperText === ""
            ? null : (upperSignificant === "" ? 0 : Number(upperSignificant));
    }
    if (i >= pattern.length || pattern[i] !== "}") return null;
    if (
        lower > _MAX_REGEX_REPEAT ||
        (upper !== null && (upper > _MAX_REGEX_REPEAT || lower > upper))
    ) return null;
    let end = i + 1;
    if (end < pattern.length && pattern[end] === "?") end++;
    return end;
}

function _hasUnsupportedRegexConstruct(pattern) {
    let i = 0;
    let inClass = false;
    let hasAlternation = false;
    let hasQuantifier = false;
    let hasStartAnchor = false;
    while (i < pattern.length) {
        const ch = pattern[i];
        if (ch === "\\") {
            if (i + 1 >= pattern.length) return true;
            const escaped = pattern[i + 1];
            const allowed = inClass
                ? _PORTABLE_REGEX_CLASS_ESCAPES
                : _PORTABLE_REGEX_LITERAL_ESCAPES;
            if (!allowed.has(escaped)) return true;
            i += 2;
            continue;
        }
        if (ch === "[" && !inClass) {
            let member = i + 1;
            if (member < pattern.length && pattern[member] === "^") member++;
            if (member < pattern.length && pattern[member] === "]") return true;
            inClass = true;
            i++;
            continue;
        }
        if (ch === "]" && inClass) {
            inClass = false;
            i++;
            continue;
        }
        if (ch === "]") return true;
        if (!inClass && ch === "$") return true;
        if (!inClass && ch === "^") hasStartAnchor = true;
        if (!inClass && ch === "|") {
            if (i === 0 || i + 1 === pattern.length) return true;
            if ("(|".includes(pattern[i - 1]) || ")|".includes(pattern[i + 1])) return true;
            hasAlternation = true;
        }
        if (!inClass && ch === "{") {
            const end = _portableRegexQuantifierEnd(pattern, i);
            if (end === null) return true;
            hasQuantifier = true;
            i = end;
            continue;
        }
        if (!inClass && ch === "}") return true;
        if (ch === "." && !inClass) return true;
        if (ch === "(" && !inClass && i + 1 < pattern.length) {
            if (pattern[i + 1] === "?" || pattern[i + 1] === ")") return true;
        }
        if (!inClass && (ch === "*" || ch === "+" || ch === "?")) {
            hasQuantifier = true;
            if (i + 1 < pattern.length && pattern[i + 1] === "+") return true;
        }
        i++;
    }
    return inClass || (hasAlternation && (hasQuantifier || hasStartAnchor));
}

function _countVariableRegexQuantifiers(pattern) {
    let count = 0;
    let i = 0;
    while (i < pattern.length) {
        const ch = pattern[i];
        if (ch === "\\") { i += 2; continue; }
        if (ch === "[") {
            const classEnd = _regexCharClassEnd(pattern, i);
            if (classEnd === null) return count;
            i = classEnd + 1;
            continue;
        }
        if (ch === "*" || ch === "+" || ch === "?") {
            count++;
            i++;
            if (i < pattern.length && pattern[i] === "?") i++;
            continue;
        }
        if (ch === "{" && i + 1 < pattern.length && _isAsciiRegexDigit(pattern[i + 1])) {
            let end = i + 2;
            while (end < pattern.length && _isAsciiRegexDigit(pattern[end])) end++;
            if (end < pattern.length && pattern[end] === ",") {
                end++;
                while (end < pattern.length && _isAsciiRegexDigit(pattern[end])) end++;
                if (end < pattern.length && pattern[end] === "}") {
                    count++;
                    i = end + 1;
                    if (i < pattern.length && pattern[i] === "?") i++;
                    continue;
                }
            }
        }
        i++;
    }
    return count;
}

function _countRegexAlternationSites(pattern) {
    const sites = new _GENO_SET();
    const stack = [0];
    let nextGroup = 1;
    let i = 0;
    while (i < pattern.length) {
        if (pattern[i] === "\\") { i += 2; continue; }
        if (pattern[i] === "[") {
            const classEnd = _regexCharClassEnd(pattern, i);
            if (classEnd === null) return sites.size;
            i = classEnd + 1;
            continue;
        }
        if (pattern[i] === "(") {
            stack.push(nextGroup++);
        } else if (pattern[i] === ")" && stack.length > 1) {
            stack.pop();
        } else if (pattern[i] === "|") {
            sites.add(stack[stack.length - 1]);
        }
        i++;
    }
    return sites.size;
}

// Detect quantified groups that contain inner quantifiers (ReDoS risk).
// Mirrors Python's `_has_nested_quantifier` in geno/_runtime_support.py.
function _hasNestedQuantifier(pattern) {
    const n = pattern.length;
    let i = 0;
    while (i < n) {
        if (pattern[i] === "\\") { i += 2; continue; }
        if (pattern[i] === ")") {
            let j = i + 1;
            while (j < n && (pattern[j] === " " || pattern[j] === "\t")) j++;
            if (j < n && (pattern[j] === "+" || pattern[j] === "*" || pattern[j] === "?" || pattern[j] === "{")) {
                // Walk backwards to find the matching '('
                let depth = 1;
                let k = i - 1;
                while (k >= 0 && depth > 0) {
                    if (pattern[k] === ")" && (k === 0 || pattern[k - 1] !== "\\")) depth++;
                    else if (pattern[k] === "(" && (k === 0 || pattern[k - 1] !== "\\")) depth--;
                    k--;
                }
                const groupStart = k + 2;
                let m = groupStart;
                while (m < i) {
                    if (pattern[m] === "\\") { m += 2; continue; }
                    if (pattern[m] === "+" || pattern[m] === "*") return true;
                    if (
                        pattern[m] === "?" &&
                        m > groupStart &&
                        pattern[m - 1] !== "("
                    ) return true;
                    if (pattern[m] === "{" && m + 1 < i && _isAsciiRegexDigit(pattern[m + 1])) return true;
                    m++;
                }
            }
        }
        i++;
    }
    return false;
}

// Detect repeated groups with overlapping top-level alternation branches.
// Mirrors Python's `_has_overlapping_alternation`.
// Repeated alternation is outside Geno's safe regex subset. Equivalent atoms
// can be spelled differently (for example `a` and `[a]`), so comparing branch
// source text cannot prove that a backtracking expression is safe.
function _hasOverlappingAlternation(pattern) {
    const n = pattern.length;
    let i = 0;
    while (i < n) {
        if (pattern[i] === "\\") { i += 2; continue; }
        if (pattern[i] === "[") {
            i++;
            while (i < n) {
                if (pattern[i] === "\\") { i += 2; continue; }
                if (pattern[i] === "]") { i++; break; }
                i++;
            }
            continue;
        }
        if (pattern[i] !== "(") { i++; continue; }

        const groupStart = i;
        let depth = 1;
        let hasAlternation = false;
        let inCharClass = false;
        let j = i + 1;
        while (j < n && depth > 0) {
            const ch = pattern[j];
            if (ch === "\\") { j += 2; continue; }
            if (inCharClass) {
                if (ch === "]") inCharClass = false;
                j++;
                continue;
            }
            if (ch === "[") inCharClass = true;
            else if (ch === "(") depth++;
            else if (ch === ")") {
                depth--;
                if (depth === 0) break;
            } else if (ch === "|") hasAlternation = true;
            j++;
        }

        if (depth !== 0) return false;

        let quantIdx = j + 1;
        while (quantIdx < n && (pattern[quantIdx] === " " || pattern[quantIdx] === "\t")) quantIdx++;
        if (
            hasAlternation &&
            quantIdx < n &&
            (pattern[quantIdx] === "+" || pattern[quantIdx] === "*" ||
                pattern[quantIdx] === "?" || pattern[quantIdx] === "{")
        ) return true;

        // Continue inside this group so quantified nested groups are checked.
        i = groupStart + 1;
    }
    return false;
}

function _regexCharClassEnd(pattern, start) {
    let i = start + 1;
    while (i < pattern.length) {
        if (pattern[i] === "\\") { i += 2; continue; }
        if (pattern[i] === "]") return i;
        i++;
    }
    return null;
}

function _regexGroupEnd(pattern, start) {
    let depth = 1;
    let i = start + 1;
    while (i < pattern.length) {
        if (pattern[i] === "\\") { i += 2; continue; }
        if (pattern[i] === "[") {
            const classEnd = _regexCharClassEnd(pattern, i);
            if (classEnd === null) return null;
            i = classEnd + 1;
            continue;
        }
        if (pattern[i] === "(") depth++;
        else if (pattern[i] === ")") {
            depth--;
            if (depth === 0) return i;
        }
        i++;
    }
    return null;
}

function _regexQuantifierEnd(pattern, start) {
    if (start >= pattern.length) return null;
    if (pattern[start] === "*" || pattern[start] === "+" || pattern[start] === "?") {
        const marker = pattern[start];
        let end = start + 1;
        if (end < pattern.length && pattern[end] === "?") end++;
        return { end, ambiguous: true, canConsume: true, required: marker === "+" };
    }
    if (pattern[start] !== "{" || start + 1 >= pattern.length) return null;

    let i = start + 1;
    while (i < pattern.length && _isAsciiRegexDigit(pattern[i])) i++;
    const hasComma = i < pattern.length && pattern[i] === ",";
    const minimumText = pattern.slice(start + 1, i).replace(/^0+/, "");
    let maximumText = minimumText;
    if (hasComma) {
        i++;
        const maximumStart = i;
        while (i < pattern.length && _isAsciiRegexDigit(pattern[i])) i++;
        maximumText = pattern.slice(maximumStart, i).replace(/^0+/, "");
    }
    if (i >= pattern.length || pattern[i] !== "}") return null;
    let end = i + 1;
    if (end < pattern.length && pattern[end] === "?") end++;
    const required = minimumText !== "" && minimumText !== "0";
    const canConsume = required || (hasComma && maximumText !== "0");
    return { end, ambiguous: hasComma, canConsume, required };
}

function _regexCharClassKey(pattern, start, end) {
    const content = pattern.slice(start + 1, end);
    const codePoints = Array.from(content);
    if (codePoints.length === 1) return "literal:" + content;
    if (codePoints.length === 2 && codePoints[0] === "\\") return "literal:" + codePoints[1];
    return "class:" + content;
}

function _regexQuantifiedAtomsOverlap(left, right) {
    if (left === right) return true;
    if (left === "literal:." || right === "literal:.") return true;
    return !left.startsWith("literal:") || !right.startsWith("literal:");
}


function _hasSequentialQuantifiedAtoms(pattern) {
    let previousKey = null;
    let i = 0;
    while (i < pattern.length) {
        const ch = pattern[i];
        let key;
        let atomEnd;

        if (ch === "|") {
            previousKey = null;
            i++;
            continue;
        }
        if (ch === "\\") {
            if (i + 1 >= pattern.length) return false;
            key = "escape:" + pattern[i + 1];
            atomEnd = i + 2;
        } else if (ch === "[") {
            const classEnd = _regexCharClassEnd(pattern, i);
            if (classEnd === null) return false;
            key = _regexCharClassKey(pattern, i, classEnd);
            atomEnd = classEnd + 1;
        } else if (ch === "(") {
            const groupEnd = _regexGroupEnd(pattern, i);
            if (groupEnd === null) return false;
            if (_hasSequentialQuantifiedAtoms(pattern.slice(i + 1, groupEnd))) {
                return true;
            }
            key = "group:" + pattern.slice(i + 1, groupEnd);
            atomEnd = groupEnd + 1;
        } else if (ch === ")" || ch === "^" || ch === "$") {
            i++;
            continue;
        } else {
            const codePoint = pattern.codePointAt(i);
            const literal = String.fromCodePoint(codePoint);
            key = "literal:" + literal;
            atomEnd = i + literal.length;
        }

        const quantifier = _regexQuantifierEnd(pattern, atomEnd);
        let quantifierEnd;
        let ambiguous;
        let canConsume;
        let required;
        if (quantifier === null) {
            quantifierEnd = atomEnd;
            ambiguous = false;
            canConsume = true;
            required = true;
        } else {
            ({ end: quantifierEnd, ambiguous, canConsume, required } = quantifier);
        }

        if (
            previousKey !== null
            && canConsume
            && _regexQuantifiedAtomsOverlap(previousKey, key)
        ) {
            return true;
        }
        if (previousKey !== null && required) previousKey = null;
        if (ambiguous && canConsume) {
            previousKey = key;
        }
        i = quantifierEnd;
    }
    return false;
}

function _validateRegexPattern(pattern, funcName) {
    if (typeof pattern !== "string") {
        throw new Error(funcName + ": pattern must be a string");
    }
    if (_stringLength(pattern) > _MAX_REGEX_PATTERN_LEN) {
        throw new Error(
            funcName + ": pattern too long (max " + _MAX_REGEX_PATTERN_LEN + " chars)"
        );
    }
    if (_BACKREF_RE.test(pattern)) {
        throw new Error(funcName + ": backreferences are not supported for safety");
    }
    if (_regexGroupDepthExceedsLimit(pattern)) {
        throw new Error(
            funcName + ": group nesting too deep (max " + _MAX_REGEX_GROUP_DEPTH + ")"
        );
    }
    if (_hasNestedQuantifier(pattern)) {
        throw new Error(funcName + ": nested quantifiers are not supported for safety");
    }
    if (_hasOverlappingAlternation(pattern)) {
        throw new Error(
            funcName + ": overlapping alternation branches are not supported for safety"
        );
    }
    if (_hasSequentialQuantifiedAtoms(pattern)) {
        throw new Error(
            funcName + ": adjacent repeated atoms are not supported for safety"
        );
    }
    if (_countVariableRegexQuantifiers(pattern) > 1) {
        throw new Error(
            funcName + ": multiple variable quantifiers are not supported for safety"
        );
    }
    if (_countRegexAlternationSites(pattern) > 1) {
        throw new Error(
            funcName + ": multiple alternation sites are not supported for safety"
        );
    }
    if (_hasUnsupportedRegexConstruct(pattern)) {
        throw new Error(
            funcName + ": advanced or encoded regex constructs are not supported for safety"
        );
    }
}

function _validateRegexText(text, funcName, argName) {
    const name = argName || "text";
    if (typeof text !== "string") {
        throw new Error(funcName + ": " + name + " must be a string");
    }
    if (_stringLength(text) > _MAX_REGEX_TEXT_LEN) {
        throw new Error(
            funcName + ": " + name + " too long (max " + _MAX_REGEX_TEXT_LEN + " chars)"
        );
    }
}

function _expandRegexReplacement(replacement, match, totalBefore) {
    const pieces = [];
    let literal = "";
    let added = 0;

    function flushLiteral() {
        if (literal === "") return;
        const size = _stringLength(literal);
        _checkStringResultSize("regex_replace", totalBefore + added + size);
        pieces.push(literal);
        added += size;
        literal = "";
    }

    for (let i = 0; i < replacement.length; i++) {
        if (
            replacement[i] === "\\" &&
            i + 1 < replacement.length &&
            replacement[i + 1] !== "0" && _isAsciiRegexDigit(replacement[i + 1])
        ) {
            flushLiteral();
            let end = i + 1;
            while (end < replacement.length && _isAsciiRegexDigit(replacement[end])) end += 1;
            const groupIndex = Number(replacement.slice(i + 1, end));
            if (!Number.isSafeInteger(groupIndex) || groupIndex >= match.length) {
                throw new Error("regex_replace: invalid replacement group reference");
            }
            const value = match[groupIndex] === undefined ? "" : match[groupIndex];
            const size = _stringLength(value);
            _checkStringResultSize("regex_replace", totalBefore + added + size);
            pieces.push(value);
            added += size;
            i = end - 1;
        } else {
            // Dollar signs and non-reference backslashes are literals in the
            // portable Geno replacement dialect.
            literal += replacement[i];
        }
    }
    flushLiteral();
    return { text: pieces.join(""), size: added };
}

function _advanceRegexEmptyMatchIndex(text, index) {
    if (index >= text.length) return index + 1;
    const codePoint = text.codePointAt(index);
    return index + (codePoint > 0xFFFF ? 2 : 1);
}

function regex_match(pattern, text) {
    _requireCap("regex", "regex_match");
    _validateRegexPattern(pattern, "regex_match");
    _validateRegexText(text, "regex_match");
    try {
        const re = new RegExp(pattern, "u");
        const m = text.match(re);
        if (m === null) return None_;
        return Some(m[0]);
    } catch (e) {
        throw new Error("regex_match: invalid pattern: " + e.message);
    }
}

function regex_find_all(pattern, text) {
    _requireCap("regex", "regex_find_all");
    _validateRegexPattern(pattern, "regex_find_all");
    _validateRegexText(text, "regex_find_all");
    let re;
    try {
        re = new RegExp(pattern, "gu");
    } catch (e) {
        throw new Error("regex_find_all: invalid pattern: " + e.message);
    }
    const result = [];
    let match;
    while ((match = re.exec(text)) !== null) {
        const value = match[0];
        _checkStringResultSize("regex_find_all", _stringLength(value));
        _checkCollectionKind("List", result.length + 1);
        result.push(value);
        if (value === "") re.lastIndex = _advanceRegexEmptyMatchIndex(text, re.lastIndex);
    }
    return result;
}

function regex_replace(pattern, replacement, text) {
    _requireCap("regex", "regex_replace");
    _validateRegexPattern(pattern, "regex_replace");
    _validateRegexText(replacement, "regex_replace", "replacement");
    _validateRegexText(text, "regex_replace");
    let re;
    try {
        re = new RegExp(pattern, "gu");
    } catch (e) {
        throw new Error("regex_replace: invalid pattern: " + e.message);
    }
    const pieces = [];
    let total = 0;
    let lastEnd = 0;
    let match;
    while ((match = re.exec(text)) !== null) {
        const prefix = text.slice(lastEnd, match.index);
        const prefixSize = _stringLength(prefix);
        _checkStringResultSize("regex_replace", total + prefixSize);
        const expanded = _expandRegexReplacement(
            replacement, match, total + prefixSize
        );
        pieces.push(prefix);
        pieces.push(expanded.text);
        total += prefixSize + expanded.size;
        lastEnd = match.index + match[0].length;
        if (match[0] === "") {
            re.lastIndex = _advanceRegexEmptyMatchIndex(text, re.lastIndex);
        }
    }
    const tail = text.slice(lastEnd);
    const tailSize = _stringLength(tail);
    _checkStringResultSize("regex_replace", total + tailSize);
    pieces.push(tail);
    return pieces.join("");
}

// =============================================================================
// JSON Builtins
// =============================================================================

function _jsonFloatToString(value, errorMessage) {
    if (!_GENO_NUMBER.isFinite(value)) {
        throw new Error(errorMessage);
    }
    return _formatFloat(value);
}

function _jsonStringLiteral(value) {
    return _escapeJsonAscii(_GENO_JSON.stringify(value));
}

function _jsonParseError(parser, message) {
    throw new Error(`json_parse: ${message} at position ${parser.index}`);
}

function _jsonSkipWhitespace(parser) {
    while (parser.index < parser.text.length) {
        const ch = parser.text[parser.index];
        if (ch !== " " && ch !== "\n" && ch !== "\r" && ch !== "\t") return;
        parser.index += 1;
    }
}

function _jsonParseLiteral(parser, literal, value) {
    if (!parser.text.startsWith(literal, parser.index)) {
        _jsonParseError(parser, `expected ${literal}`);
    }
    parser.index += literal.length;
    return value;
}

function _jsonParseStringToken(parser) {
    const start = parser.index;
    if (parser.text[start] !== "\"") {
        _jsonParseError(parser, "expected string");
    }
    parser.index += 1;
    let escaped = false;
    while (parser.index < parser.text.length) {
        const ch = parser.text[parser.index];
        if (escaped) {
            escaped = false;
            parser.index += 1;
            continue;
        }
        if (ch === "\\") {
            escaped = true;
            parser.index += 1;
            continue;
        }
        if (ch === "\"") {
            parser.index += 1;
            const token = parser.text.slice(start, parser.index);
            try {
                return _GENO_JSON.parse(token);
            } catch (error) {
                throw new Error(
                    `json_parse: invalid string literal at position ${start}: ${error.message}`,
                );
            }
        }
        parser.index += 1;
    }
    _jsonParseError(parser, "unterminated string");
}

function _jsonParseNumber(parser) {
    const rest = parser.text.slice(parser.index);
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(rest);
    if (match === null) {
        _jsonParseError(parser, "expected number");
    }
    const token = match[0];
    parser.index += token.length;
    const isInt = !/[.eE]/.test(token);
    if (isInt) {
        return {
            _tag: "JsonInt",
            value: _fromSafeJsBigInt(BigInt(token), "json_parse integer literal"),
        };
    }
    const value = _GENO_NUMBER(token);
    if (!_GENO_NUMBER.isFinite(value)) {
        throw new Error("json_parse: non-finite JSON number");
    }
    return { _tag: "JsonFloat", value };
}

function _jsonParseArray(parser) {
    parser.index += 1;
    const items = [];
    _jsonSkipWhitespace(parser);
    if (parser.text[parser.index] === "]") {
        parser.index += 1;
        return { _tag: "JsonArray", items };
    }
    while (true) {
        const item = _jsonParseValue(parser);
        _checkCollectionKind("List", items.length + 1);
        items.push(item);
        _jsonSkipWhitespace(parser);
        const ch = parser.text[parser.index];
        if (ch === "]") {
            parser.index += 1;
            return { _tag: "JsonArray", items };
        }
        if (ch !== ",") {
            _jsonParseError(parser, "expected ',' or ']'");
        }
        parser.index += 1;
    }
}

function _jsonParseObject(parser) {
    parser.index += 1;
    const entries = [];
    const entryIndexes = new _GENO_MAP();
    _jsonSkipWhitespace(parser);
    if (parser.text[parser.index] === "}") {
        parser.index += 1;
        return { _tag: "JsonObject", entries };
    }
    while (true) {
        const key = _jsonParseStringToken(parser);
        _checkCollectionKind("String", _stringLength(key));
        _jsonSkipWhitespace(parser);
        if (parser.text[parser.index] !== ":") {
            _jsonParseError(parser, "expected ':'");
        }
        parser.index += 1;
        const value = _jsonParseValue(parser);
        if (entryIndexes.has(key)) {
            entries[entryIndexes.get(key)][1] = value;
        } else {
            _checkCollectionKind("Map", entries.length + 1);
            entryIndexes.set(key, entries.length);
            entries.push([key, value]);
        }
        _jsonSkipWhitespace(parser);
        const ch = parser.text[parser.index];
        if (ch === "}") {
            parser.index += 1;
            return { _tag: "JsonObject", entries };
        }
        if (ch !== ",") {
            _jsonParseError(parser, "expected ',' or '}'");
        }
        parser.index += 1;
        _jsonSkipWhitespace(parser);
    }
}

function _jsonParseValue(parser) {
    _jsonSkipWhitespace(parser);
    const ch = parser.text[parser.index];
    if (ch === "\"") {
        const value = _jsonParseStringToken(parser);
        _checkCollectionKind("String", _stringLength(value));
        return { _tag: "JsonString", value };
    }
    if (ch === "{") return _jsonParseObject(parser);
    if (ch === "[") return _jsonParseArray(parser);
    if (ch === "t") return _jsonParseLiteral(parser, "true", { _tag: "JsonBool", value: true });
    if (ch === "f") return _jsonParseLiteral(parser, "false", { _tag: "JsonBool", value: false });
    if (ch === "n") return _jsonParseLiteral(parser, "null", { _tag: "JsonNull" });
    if (ch === "-" || (ch >= "0" && ch <= "9")) return _jsonParseNumber(parser);
    _jsonParseError(parser, "unexpected token");
}

const _MAX_JSON_NESTING_DEPTH = 128;

function _ensureJsonNestingLimit(text) {
    let depth = 0;
    let inString = false;
    let escaped = false;
    for (const ch of text) {
        if (inString) {
            if (escaped) escaped = false;
            else if (ch === "\\") escaped = true;
            else if (ch === '"') inString = false;
            continue;
        }
        if (ch === '"') {
            inString = true;
        } else if (ch === "[" || ch === "{") {
            depth += 1;
            if (depth > _MAX_JSON_NESTING_DEPTH) {
                throw new Error(
                    "json_parse: nested too deeply (max "
                    + _MAX_JSON_NESTING_DEPTH + " levels)",
                );
            }
        } else if ((ch === "]" || ch === "}") && depth > 0) {
            depth -= 1;
        }
    }
}

function _jsonParseText(text) {
    const parser = { text, index: 0 };
    const value = _jsonParseValue(parser);
    _jsonSkipWhitespace(parser);
    if (parser.index !== text.length) {
        _jsonParseError(parser, "unexpected trailing input");
    }
    return value;
}

function _jsonValueToString(value, context) {
    if (!value || !value._tag) {
        throw new Error(`${context}: expected JsonValue`);
    }
    switch (value._tag) {
        case "JsonNull":
            return "null";
        case "JsonBool":
            return value.value ? "true" : "false";
        case "JsonInt":
            return _GENO_STRING(_requireSafeJsInt(value.value, `${context} JsonInt`));
        case "JsonFloat":
            return _jsonFloatToString(
                value.value,
                `${context}: JsonFloat must be finite`,
            );
        case "JsonString":
            return _jsonStringLiteral(value.value);
        case "JsonArray":
            return "[" + value.items.map((item) => _jsonValueToString(item, context)).join(",") + "]";
        case "JsonObject":
            return "{" + value.entries.map((entry) => (
                _jsonStringLiteral(entry[0]) + ":" + _jsonValueToString(entry[1], context)
            )).join(",") + "}";
        default:
            throw new Error(`${context}: unknown JsonValue constructor: ${value._tag}`);
    }
}

function _jsonValueToPython(value) {
    if (!value || !value._tag) {
        throw new Error("json_stringify: expected JsonValue");
    }
    switch (value._tag) {
        case "JsonNull": return null;
        case "JsonBool": return value.value;
        case "JsonInt": return _requireSafeJsInt(value.value, "json_stringify JsonInt");
        case "JsonFloat":
            if (!_GENO_NUMBER.isFinite(value.value)) {
                throw new Error("json_stringify: JsonFloat must be finite");
            }
            return value.value;
        case "JsonString": return value.value;
        case "JsonArray": return value.items.map(_jsonValueToPython);
        case "JsonObject": {
            const result = {};
            for (const entry of value.entries) {
                result[entry[0]] = _jsonValueToPython(entry[1]);
            }
            return result;
        }
        default: throw new Error("json_stringify: unknown JsonValue constructor: " + value._tag);
    }
}

function _escapeJsonAscii(text) {
    let result = "";
    for (const ch of _stringCodePoints(text)) {
        const cp = ch.codePointAt(0);
        if (cp <= 0x7f) {
            result += ch;
        } else if (cp <= 0xffff) {
            result += "\\u" + cp.toString(16).padStart(4, "0");
        } else {
            const shifted = cp - 0x10000;
            const high = 0xd800 + (shifted >> 10);
            const low = 0xdc00 + (shifted & 0x3ff);
            result += "\\u" + high.toString(16).padStart(4, "0");
            result += "\\u" + low.toString(16).padStart(4, "0");
        }
    }
    return result;
}

function json_parse(text) {
    try {
        _ensureJsonNestingLimit(text);
        return _checkCollectionSize({ _tag: "Ok", value: _jsonParseText(text) });
    } catch (e) {
        if (String(e.message).includes("size exceeds limit")) throw e;
        return _checkCollectionSize(Err(e.message));
    }
}

function json_stringify(value) {
    _checkCollectionSize(value);
    const result = _jsonValueToString(value, "json_stringify");
    _checkStringResultSize("json_stringify", _stringLength(result));
    return result;
}

function json_stringify_pretty(value, indent) {
    if (!Number.isInteger(indent)) {
        throw new Error("json_stringify_pretty: indent must be Int");
    }
    _checkCollectionSize(value);
    const obj = _jsonValueToPython(value);
    let result;
    if (indent <= 0) {
        result = _GENO_JSON.stringify(obj);
    } else {
        result = _GENO_JSON.stringify(obj, null, indent);
    }
    _checkStringResultSize("json_stringify_pretty", _stringLength(result));
    return result;
}

function _genoValueToJs(value) {
    if (value === null || value === undefined) return null;
    if (typeof value === "number") {
        if (!_GENO_NUMBER.isFinite(value)) {
            throw new Error("json_to_string: Float must be finite");
        }
        return value;
    }
    if (typeof value === "boolean" || typeof value === "string") return value;
    if (Array.isArray(value)) {
        // Tuple (array of 2 used for pairs) or list
        return value.map(_genoValueToJs);
    }
    if (value && typeof value === "object" && value._tag !== undefined) {
        // JsonValue constructors
        if (["JsonNull","JsonBool","JsonInt","JsonFloat","JsonString","JsonArray","JsonObject"].includes(value._tag)) {
            return _jsonValueToPython(value);
        }
        if (value._tag === "None") return null;
        if (value._tag === "Some") return _genoValueToJs(value.value);
        if (value._tag === "Ok") return _genoValueToJs(value.value);
        if (value._tag === "Err") return { error: _genoValueToJs(value.error) };
        // Generic ADT
        const result = { _tag: value._tag };
        for (const [k, v] of Object.entries(value)) {
            if (k !== "_tag") result[k] = _genoValueToJs(v);
        }
        return result;
    }
    if (value instanceof _GENO_MAP) {
        const result = {};
        for (const [k, v] of value.entries()) {
            result[_GENO_STRING(k)] = _genoValueToJs(v);
        }
        return result;
    }
    if (typeof value === "object") {
        const result = {};
        for (const [k, v] of Object.entries(value)) {
            result[k] = _genoValueToJs(v);
        }
        return result;
    }
    return _GENO_STRING(value);
}

function _genoValueToJsonString(value) {
    if (value === null || value === undefined) return "null";
    if (typeof value === "number") {
        if (!_GENO_NUMBER.isFinite(value)) {
            throw new Error("json_to_string: Float must be finite");
        }
        return _GENO_JSON.stringify(value);
    }
    if (typeof value === "boolean") return value ? "true" : "false";
    if (typeof value === "string") return _jsonStringLiteral(value);
    if (
        value instanceof GenoArray
        || value instanceof GenoMutableMap
        || value instanceof GenoVec
        || value instanceof GenoSet
    ) {
        return _jsonStringLiteral(_stringifyValue(value));
    }
    if (Array.isArray(value)) {
        return "[" + value.map(_genoValueToJsonString).join(",") + "]";
    }
    if (value && typeof value === "object" && value._tag !== undefined) {
        if (["JsonNull","JsonBool","JsonInt","JsonFloat","JsonString","JsonArray","JsonObject"].includes(value._tag)) {
            return _jsonValueToString(value, "json_to_string");
        }
        if (value._tag === "None") return "null";
        if (value._tag === "Some") return _genoValueToJsonString(value.value);
        if (value._tag === "Ok") return _genoValueToJsonString(value.value);
        if (value._tag === "Err") {
            return "{\"error\":" + _genoValueToJsonString(value.error) + "}";
        }
        const entries = [];
        entries.push(_jsonStringLiteral("_tag") + ":" + _jsonStringLiteral(value._tag));
        for (const [k, v] of Object.entries(value)) {
            if (k !== "_tag") entries.push(_jsonStringLiteral(k) + ":" + _genoValueToJsonString(v));
        }
        return "{" + entries.join(",") + "}";
    }
    if (value instanceof _GENO_MAP) {
        const entries = [];
        for (const [k, v] of value.entries()) {
            entries.push(_jsonStringLiteral(_GENO_STRING(k)) + ":" + _genoValueToJsonString(v));
        }
        return "{" + entries.join(",") + "}";
    }
    if (typeof value === "object") {
        const entries = [];
        for (const [k, v] of Object.entries(value)) {
            entries.push(_jsonStringLiteral(k) + ":" + _genoValueToJsonString(v));
        }
        return "{" + entries.join(",") + "}";
    }
    return _jsonStringLiteral(_GENO_STRING(value));
}

function json_to_string(value) {
    _checkCollectionSize(value);
    const result = _genoValueToJsonString(value);
    _checkStringResultSize("json_to_string", _stringLength(result));
    return result;
}

// =============================================================================
// CSV/TOML Builtins
// =============================================================================

function _csvAppendFieldChar(funcName, field, ch) {
    _checkStringResultSize(funcName, _stringLength(field) + _stringLength(ch));
    return field + ch;
}

function _csvPushField(funcName, row, field) {
    _checkStringResultSize(funcName, _stringLength(field));
    _checkCollectionKind("List", row.length + 1);
    row.push(field);
}

function _forEachCsvRow(text, funcName, onRow) {
    let row = [];
    let field = "";
    let inQuotes = false;
    let fieldStarted = false;
    const chars = _stringCodePoints(text);
    for (let i = 0; i < chars.length; i++) {
        const ch = chars[i];
        if (inQuotes) {
            if (ch === '"' && i + 1 < chars.length && chars[i + 1] === '"') {
                field = _csvAppendFieldChar(funcName, field, '"');
                i++;
            } else if (ch === '"') {
                inQuotes = false;
            } else {
                field = _csvAppendFieldChar(funcName, field, ch);
            }
        } else if (ch === '"' && !fieldStarted) {
            fieldStarted = true;
            inQuotes = true;
        } else if (ch === ',') {
            _csvPushField(funcName, row, field);
            field = "";
            fieldStarted = false;
        } else if (ch === '\n' || (ch === '\r' && i + 1 < chars.length && chars[i + 1] === '\n')) {
            if (fieldStarted || row.length > 0) {
                _csvPushField(funcName, row, field);
            }
            field = "";
            fieldStarted = false;
            _checkCollectionKind("List", row.length);
            onRow(row);
            row = [];
            if (ch === '\r') i++;
        } else if (ch === '\r') {
            if (fieldStarted || row.length > 0) {
                _csvPushField(funcName, row, field);
            }
            field = "";
            fieldStarted = false;
            _checkCollectionKind("List", row.length);
            onRow(row);
            row = [];
        } else {
            fieldStarted = true;
            field = _csvAppendFieldChar(funcName, field, ch);
        }
    }
    if (fieldStarted || row.length > 0) {
        _csvPushField(funcName, row, field);
        _checkCollectionKind("List", row.length);
        onRow(row);
    }
}

function csv_parse(text) {
    const rows = [];
    _forEachCsvRow(text, "csv_parse", row => {
        _checkCollectionKind("List", rows.length + 1);
        rows.push(row);
    });
    return rows;
}

function csv_parse_with_headers(text) {
    let headers = null;
    const result = [];
    _forEachCsvRow(text, "csv_parse_with_headers", row => {
        if (headers === null) {
            headers = row;
            return;
        }
        const obj = new _GENO_MAP();
        for (let j = 0; j < headers.length; j++) {
            _checkStringResultSize("csv_parse_with_headers", _stringLength(headers[j]));
            const value = j < row.length ? row[j] : "";
            _checkStringResultSize("csv_parse_with_headers", _stringLength(value));
            if (_mapFindKey(obj, headers[j]) === _MAP_MISSING) {
                _checkCollectionKind("Map", obj.size + 1);
            }
            _mapSet(obj, headers[j], value);
        }
        _checkCollectionKind("List", result.length + 1);
        result.push(obj);
    });
    return result;
}

function toml_parse(_text) {
    return _checkCollectionSize(Err("TOML parsing not available in JS target"));
}

// =============================================================================
// Process Execution Builtins
// =============================================================================

function _splitProcessCommand(command) {
    if (typeof command !== "string") {
        throw new Error("command must be a string");
    }

    const argv = [];
    let token = "";
    let tokenStarted = false;
    let quote = null;

    for (let i = 0; i < command.length; i++) {
        const ch = command[i];
        if (quote === null) {
            if (/\s/.test(ch)) {
                if (tokenStarted) {
                    argv.push(token);
                    token = "";
                    tokenStarted = false;
                }
                continue;
            }
            if (ch === "'" || ch === '"') {
                quote = ch;
                tokenStarted = true;
                continue;
            }
            if (ch === "\\") {
                if (i + 1 >= command.length) {
                    throw new Error("No escaped character");
                }
                token += command[++i];
                tokenStarted = true;
                continue;
            }
            token += ch;
            tokenStarted = true;
            continue;
        }

        if (quote === "'") {
            if (ch === "'") {
                quote = null;
            } else {
                token += ch;
            }
            continue;
        }

        if (ch === '"') {
            quote = null;
            continue;
        }
        if (ch === "\\") {
            if (i + 1 >= command.length) {
                throw new Error("No escaped character");
            }
            const next = command[i + 1];
            if (next === "\n") {
                i++;
            } else if (next === "$" || next === "`" || next === '"' || next === "\\") {
                token += next;
                i++;
            } else {
                token += ch;
            }
            continue;
        }
        token += ch;
    }

    if (quote !== null) {
        throw new Error("No closing quotation");
    }
    if (tokenStarted) {
        argv.push(token);
    }
    if (argv.length === 0) {
        throw new Error("empty command");
    }
    return argv;
}

function _processOutput(value) {
    return typeof value === "string" ? value : "";
}

function _processResultFromSpawn(result) {
    const exitCode = typeof result.status === "number" ? result.status : 1;
    return _checkCollectionSize(Ok(ProcessResult(
        exitCode,
        _processOutput(result.stdout),
        _processOutput(result.stderr)
    )));
}

function _runProcessArgv(cp, argv, stdin = null) {
    const options = {
        encoding: "utf-8",
        env: _processEnv(),
        shell: false,
        timeout: 30000,
    };
    if (stdin !== null) {
        options.input = stdin;
    }

    const result = cp.spawnSync(argv[0], argv.slice(1), options);
    if (result.error) {
        if (result.error.code === "ETIMEDOUT") {
            return { _tag: "Err", error: "Process timed out" };
        }
        return { _tag: "Err", error: result.error.message };
    }
    return _processResultFromSpawn(result);
}

function exec(command) {
    _requireCap("process", "exec");
    if (typeof require === "undefined") {
        return { _tag: "Err", error: "process execution not available in browser context" };
    }
    const cp = require("child_process");
    let argv;
    try {
        argv = _splitProcessCommand(command);
    } catch (e) {
        return { _tag: "Err", error: e.message };
    }
    return _runProcessArgv(cp, argv);
}

function exec_with_input(command, stdin) {
    _requireCap("process", "exec_with_input");
    if (typeof require === "undefined") {
        return { _tag: "Err", error: "process execution not available in browser context" };
    }
    const cp = require("child_process");
    let argv;
    try {
        argv = _splitProcessCommand(command);
    } catch (e) {
        return { _tag: "Err", error: e.message };
    }
    return _runProcessArgv(cp, argv, stdin);
}

// =============================================================================
// Environment Variable Builtins
// =============================================================================

function env_get(name) {
    _requireCap("env", "env_get");
    const value = (typeof process !== "undefined" && process.env) ? process.env[name] : undefined;
    if (value === undefined) {
        return { _tag: "None" };
    }
    return _checkCollectionSize(Some(value));
}

function env_get_or(name, defaultValue) {
    _requireCap("env", "env_get_or");
    const value = (typeof process !== "undefined" && process.env) ? process.env[name] : undefined;
    const result = value !== undefined ? value : defaultValue;
    _checkStringResultSize("env_get_or", _stringLength(result));
    return result;
}

function cli_args() {
    _requireCap("env", "cli_args");
    if (typeof process === "undefined") return [];
    const argv = process.argv;
    const idx = argv.indexOf("--");
    if (idx === -1) return [];
    return _checkCollectionSize(argv.slice(idx + 1));
}

// =============================================================================
// Extended Collection Builtins
// =============================================================================

function zip(list1, list2) {
    const len = _GENO_MATH.min(list1.length, list2.length);
    const result = [];
    for (let i = 0; i < len; i++) {
        result.push([list1[i], list2[i]]);
    }
    return result;
}

function enumerate(lst) {
    return _checkCollectionSize(lst.map((v, i) => [i, v]));
}

function flat_map(lst, fn) {
    const result = [];
    for (const item of lst) {
        const mapped = fn(item);
        if (!Array.isArray(mapped)) throw new Error("flat_map: function must return a list");
        _checkCollectionKind("List", result.length + mapped.length);
        for (const x of mapped) {
            result.push(x);
        }
    }
    return result;
}

function contains_substring(text, substring) {
    return text.includes(substring);
}

function repeat_string(text, count) {
    if (count < 0) throw new Error("repeat_string: count must be non-negative");
    if (_stringLength(text) * count > _MAX_COLLECTION_SIZE) {
        throw new Error("repeat_string: result would exceed collection size limit");
    }
    return text.repeat(count);
}

function string_trim(text) { return _trimPythonWhitespace(text, true, true); }
function string_trim_start(text) { return _trimPythonWhitespace(text, true, false); }
function string_trim_end(text) { return _trimPythonWhitespace(text, false, true); }
function _validatePadFill(funcName, fill_char) {
    if (typeof fill_char !== "string") {
        throw new Error(funcName + ": fill_char must be a string");
    }
    if (_stringLength(fill_char) !== 1) {
        throw new Error(funcName + ": fill_char must be a single character");
    }
}
function string_pad_left(text, width, fill_char) {
    _validatePadFill("string_pad_left", fill_char);
    _checkStringResultSize("string_pad_left", _GENO_MATH.max(_stringLength(text), width));
    return _stringPad(text, width, fill_char, true);
}
function string_pad_right(text, width, fill_char) {
    _validatePadFill("string_pad_right", fill_char);
    _checkStringResultSize("string_pad_right", _GENO_MATH.max(_stringLength(text), width));
    return _stringPad(text, width, fill_char, false);
}
function string_char_at(text, index) { return _stringCharAt(text, index); }
function string_index_of(text, sub) {
    return _requireSafeJsInt(_stringIndexOf(text, sub), "string_index_of result");
}
function string_last_index_of(text, sub) {
    return _requireSafeJsInt(_stringLastIndexOf(text, sub), "string_last_index_of result");
}
function string_repeat(text, count) {
    if (count < 0) throw new Error("string_repeat: count must be non-negative");
    if (_stringLength(text) * count > _MAX_COLLECTION_SIZE) {
        throw new Error("string_repeat: result would exceed collection size limit");
    }
    return text.repeat(count);
}
function string_substring(text, start, stop) {
    return _stringSubstring(text, start, stop, "string_substring");
}

function string_split(text, delimiter) {
    _checkCollectionKind("List", _splitResultCount("string_split", text, delimiter));
    const result = text.split(delimiter);
    return result;
}
function string_join(parts, separator) {
    return _joinStringsUnderLimit("string_join", parts, separator);
}
function string_replace(text, oldStr, newStr) {
    _checkStringResultSize("string_replace", _replaceResultSize(text, oldStr, newStr));
    return _replacePython(text, oldStr, newStr);
}
function string_to_upper(text) { return _checkedPythonCase("string_to_upper", text, true); }
function string_to_lower(text) { return _checkedPythonCase("string_to_lower", text, false); }
function string_starts_with(text, prefix) { return text.startsWith(prefix); }
function string_ends_with(text, suffix) { return text.endsWith(suffix); }
function string_contains(text, substring) { return text.includes(substring); }
function string_split_once(text, delimiter) {
    if (delimiter === "") throw new Error("string_split_once: delimiter cannot be empty");
    const idx = text.indexOf(delimiter);
    if (idx === -1) return None_;
    return Some([text.slice(0, idx), text.slice(idx + delimiter.length)]);
}

function math_abs(x) { return _GENO_MATH.abs(x); }
function math_min(a, b) { return _GENO_MATH.min(a, b); }
function math_max(a, b) { return _GENO_MATH.max(a, b); }
function math_clamp(v, lo, hi) { return _GENO_MATH.max(lo, _GENO_MATH.min(hi, v)); }
function math_floor(x) { return _requireSafeJsInt(_GENO_MATH.floor(x), "math_floor result"); }
function math_ceil(x) { return _requireSafeJsInt(_GENO_MATH.ceil(x), "math_ceil result"); }
function math_round(x) { return _roundNearest(x, "math_round result"); }
function math_sqrt(x) {
    if (typeof x !== 'number') {
        throw new Error("math_sqrt: expected number, got " + typeof x);
    }
    if (x < 0) {
        throw new Error("math_sqrt: argument must be non-negative");
    }
    return _GENO_MATH.sqrt(x);
}
function math_log(x) {
    if (typeof x !== 'number') {
        throw new Error("math_log: expected number, got " + typeof x);
    }
    if (x <= 0) {
        throw new Error("math_log: argument must be positive");
    }
    return _GENO_MATH.log(x);
}
function math_sin(x) { return _GENO_MATH.sin(x); }
function math_cos(x) { return _GENO_MATH.cos(x); }
function math_pi() { return _GENO_MATH.PI; }
function math_e() { return _GENO_MATH.E; }
function math_random_int(lo, hi) {
    _requireCap("random", "math_random_int");
    const min = _requireSafeJsInt(lo, "math_random_int lower bound");
    const max = _requireSafeJsInt(hi, "math_random_int upper bound");
    if (min > max) throw new Error("math_random_int: lower bound must be <= upper bound");
    const span = _fromSafeJsBigInt(
        _toSafeJsBigInt(max, "math_random_int upper bound")
        - _toSafeJsBigInt(min, "math_random_int lower bound")
        + 1n,
        "math_random_int span",
    );
    return _requireSafeJsInt(
        _GENO_MATH.floor(_GENO_MATH.random() * span) + min,
        "math_random_int result",
    );
}
function math_random_float() {
    _requireCap("random", "math_random_float");
    return _GENO_MATH.random();
}

// =============================================================================
// Result stdlib
// =============================================================================

function result_map(result, f) {
    if (result._tag === 'Ok') return Ok(f(result.value));
    return result;
}

function result_map_err(result, f) {
    if (result._tag === 'Err') return Err(f(result.error));
    return result;
}

function result_and_then(result, f) {
    if (result._tag === 'Ok') return f(result.value);
    return result;
}

function result_unwrap_or(result, defaultVal) {
    if (result._tag === 'Ok') return result.value;
    return defaultVal;
}

function result_is_ok(result) {
    return result._tag === 'Ok';
}

function result_is_err(result) {
    return result._tag === 'Err';
}

function result_to_option(result) {
    if (result._tag === 'Ok') return Some(result.value);
    return None_;
}

// =============================================================================
// Option stdlib
// =============================================================================

function option_map(option, f) {
    if (option._tag === 'Some') return Some(f(option.value));
    return None_;
}

function option_and_then(option, f) {
    if (option._tag === 'Some') return f(option.value);
    return None_;
}

function option_unwrap_or(option, defaultVal) {
    if (option._tag === 'Some') return option.value;
    return defaultVal;
}

function option_is_some(option) {
    return option._tag === 'Some';
}

function option_is_none(option) {
    return option._tag === 'None';
}

function option_flatten(option) {
    if (option._tag === 'Some' && option.value && (option.value._tag === 'Some' || option.value._tag === 'None')) {
        return option.value;
    }
    return None_;
}

function option_to_result(option, err) {
    if (option._tag === 'Some') return Ok(option.value);
    return Err(err);
}

// =============================================================================
// Path stdlib
// =============================================================================

function path_join(base, child) {
    let result;
    if (child.startsWith('/')) result = child;
    else if (base === '') result = child;
    else if (base.endsWith('/')) result = base + child;
    else result = base + '/' + child;
    _checkStringResultSize("path_join", _stringLength(result));
    return result;
}

function path_parent(path) {
    const idx = path.lastIndexOf('/');
    if (idx < 0) return '';
    if (idx === 0) return '/';
    return path.substring(0, idx);
}

function path_filename(path) {
    const idx = path.lastIndexOf('/');
    return idx < 0 ? path : path.substring(idx + 1);
}

function path_extension(path) {
    const base = path_filename(path);
    const idx = base.lastIndexOf('.');
    if (idx <= 0) return '';
    return base.substring(idx);
}

function path_is_absolute(path) {
    return path.startsWith('/') || /^[A-Za-z]:\//.test(path);
}

// =============================================================================
// DateTime stdlib
// =============================================================================

function datetime_now() {
    _requireCap("clock", "datetime_now");
    return _requireSafeJsInt(_GENO_MATH.floor(_GENO_DATE.now() / 1000), "datetime_now result");
}

function datetime_format(timestamp, fmt) {
    _requireCap("clock", "datetime_format");
    // Delegate to clock_format so both backends and the interpreter honor the
    // same narrow directive contract (%Y %m %d %H %M %S %%).
    return clock_format(timestamp, fmt);
}

function datetime_parse(text, fmt) {
    _requireCap("clock", "datetime_parse");
    const result = clock_parse(text, fmt);
    if (result && result._tag === 'Some') {
        return Some(_requireSafeJsInt(_GENO_MATH.floor(result.value), "datetime_parse result"));
    }
    return None_;
}

function datetime_elapsed(start, end_time) {
    _requireCap("clock", "datetime_elapsed");
    return _requireSafeJsInt(end_time - start, "datetime_elapsed result");
}

// =============================================================================
// Serve stdlib (browser stub)
// =============================================================================

function http_route(method, path, handler) {
    _requireCap("serve", "http_route");
    return {_tag: "Err", error: "HTTP server not available in browser context"};
}

function http_listen(port) {
    _requireCap("serve", "http_listen");
    return {_tag: "Err", error: "HTTP server not available in browser context"};
}

function http_respond(status, headers, body) {
    _requireCap("serve", "http_respond");
    return Object.freeze({_tag: "HttpResponse", status: status, body: body, headers: headers});
}

function map_from_list(pairs) {
    const m = new _GENO_MAP();
    for (const pair of pairs) {
        if (!Array.isArray(pair) || pair.length !== 2) {
            throw new Error("map_from_list: each element must be a (key, value) pair");
        }
        if (_mapFindKey(m, pair[0]) === _MAP_MISSING) {
            _checkCollectionKind("Map", m.size + 1);
        }
        _mapSet(m, pair[0], pair[1]);
    }
    return m;
}
function map_merge(m1, m2) {
    let expected = m1.size;
    for (const key of m2.keys()) {
        if (_mapFindKey(m1, key) === _MAP_MISSING) expected += 1;
    }
    _checkCollectionKind("Map", expected);
    const result = _mapClone(m1);
    for (const [k, v] of m2) _mapSet(result, k, v);
    return result;
}
function map_filter_map(m, pred) {
    const result = new _GENO_MAP();
    for (const [k, v] of m) if (pred(k, v)) _mapSet(result, k, v);
    return result;
}
function map_map_values(m, f) {
    const result = new _GENO_MAP();
    for (const [k, v] of m) _mapSet(result, k, f(v));
    return result;
}
function map_entries(m) {
    _checkCollectionKind("List", m.size);
    return Array.from(m.entries()).map(([k, v]) => [k, v]);
}
function map_from_entries(entries) {
    const m = new _GENO_MAP();
    for (const entry of entries) {
        if (!Array.isArray(entry) || entry.length !== 2) {
            throw new Error("map_from_entries: each element must be a (key, value) pair");
        }
        if (_mapFindKey(m, entry[0]) === _MAP_MISSING) {
            _checkCollectionKind("Map", m.size + 1);
        }
        _mapSet(m, entry[0], entry[1]);
    }
    return m;
}

function list_zip(xs, ys) {
    const result = [];
    const len = _GENO_MATH.min(xs.length, ys.length);
    for (let i = 0; i < len; i++) result.push([xs[i], ys[i]]);
    return result;
}
function list_enumerate(xs) {
    return _checkCollectionSize(xs.map((x, i) => [i, x]));
}
function list_all(xs, pred) {
    for (const x of xs) if (!pred(x)) return false;
    return true;
}
function list_flatten(xss) {
    const result = [];
    for (const xs of xss) {
        _checkCollectionKind("List", result.length + xs.length);
        for (const x of xs) result.push(x);
    }
    return result;
}
function list_chunk(xs, n) {
    if (n <= 0) throw new Error("list_chunk: chunk size must be positive");
    const result = [];
    for (let i = 0; i < xs.length; i += n) result.push(xs.slice(i, i + n));
    return result;
}
function list_take(xs, n) { return xs.slice(0, _GENO_MATH.max(0, n)); }
function list_drop(xs, n) { return xs.slice(_GENO_MATH.max(0, n)); }
function list_find(xs, pred) {
    for (const x of xs) if (pred(x)) return Some(x);
    return None_;
}
function list_find_index(xs, pred) {
    for (let i = 0; i < xs.length; i++) if (pred(xs[i])) return Some(_checkIntegerBits(i));
    return None_;
}
function list_any(xs, pred) {
    for (const x of xs) if (pred(x)) return true;
    return false;
}
function list_fold_right(xs, init, f) {
    let acc = init;
    for (let i = xs.length - 1; i >= 0; i--) acc = f(xs[i], acc);
    return acc;
}
function list_intersperse(xs, sep) {
    if (xs.length === 0) return [];
    _checkCollectionKind("List", xs.length * 2 - 1);
    const result = [xs[0]];
    for (let i = 1; i < xs.length; i++) { result.push(sep); result.push(xs[i]); }
    return result;
}
function list_group_by(xs, key_fn) {
    const groups = [];
    for (const x of xs) {
        const k = key_fn(x);
        let bucket = null;
        for (const group of groups) {
            if (_valuesEqual(group[0], k)) {
                bucket = group;
                break;
            }
        }
        if (bucket === null) {
            bucket = [k, []];
            groups.push(bucket);
        }
        bucket[1].push(x);
    }
    return groups;
}

function list_length(xs) { return _requireSafeJsInt(xs.length, "list_length result"); }
function list_map(xs, transform) {
    const result = xs.map(transform);
    _checkCollectionSize(result);
    return result;
}
function list_filter(xs, predicate) { return xs.filter(predicate); }

function clamp(value, min_val, max_val) {
    return _GENO_MATH.max(min_val, _GENO_MATH.min(max_val, value));
}
