# Benchmark Problem Quality Criteria

This document defines the quality standards for problems in the Geno LLM benchmark.

## 1. Problem Requirements

### 1.1 Completeness
Every problem specification MUST include:
- [ ] Unique ID in format `PROB-XXX`
- [ ] Descriptive name
- [ ] Difficulty level (trivial/easy/medium/hard)
- [ ] Domain classification
- [ ] Clear problem description
- [ ] Function signature with typed inputs/outputs
- [ ] At least 2 visible examples
- [ ] At least 4 hidden test cases
- [ ] Working Geno solution
- [ ] Equivalent Python solution

### 1.2 Description Quality
- Description should be clear and unambiguous
- Include input/output formats explicitly
- Specify edge case handling
- Avoid implementation hints (let LLM discover approach)

### 1.3 Example Quality
Visible examples should:
- Demonstrate the basic behavior
- Include at least one "typical" case
- NOT reveal tricky edge cases

Hidden tests should:
- Cover edge cases (empty input, single element, etc.)
- Include boundary conditions
- Test negative numbers where applicable
- Include performance stress tests for harder problems

## 2. Difficulty Calibration

### Trivial (Target: 90-95% LLM success)
- Single concept (one function, one loop)
- < 10 lines of solution code
- No recursion required
- Direct translation of specification

Examples: add two numbers, is_even, list_length

### Easy (Target: 75-90% LLM success)
- 2-3 concepts combined
- 10-30 lines of solution code
- Simple recursion allowed
- Single loop with basic logic

Examples: factorial, find_max, reverse_list, count_occurrences

### Medium (Target: 50-75% LLM success)
- Multiple constructs required
- 30-60 lines of solution code
- Nested loops or moderate recursion
- Algorithm knowledge helpful

Examples: binary_search, two_sum, merge_sorted, sorting algorithms

### Hard (Target: 25-50% LLM success)
- Complex algorithms required
- 60-150 lines of solution code
- Non-trivial recursion or dynamic programming
- Requires careful analysis

Examples: quicksort, merge_sort, complex DP problems

### Expert (Target: <25% LLM success)
- System-level complexity
- 150+ lines of solution code
- Requires deep algorithmic or systems knowledge
- Multiple interacting subsystems

Examples: complex graph algorithms, system-level challenges

## 3. Domain Coverage

Target distribution across domains:
- Arrays: 20%
- Strings: 15%
- Math: 15%
- Sorting/Searching: 15%
- Recursion: 10%
- Dynamic Programming: 10%
- Pattern Matching: 5%
- Trees/Graphs: 5%
- Systems: 5%

## 4. Construct Coverage

Each problem should specify which Geno constructs it tests:

### Core Constructs
- [ ] Function definition (`func ... end func`)
- [ ] Variable binding (`let`, `var`)
- [ ] Conditional (`if ... then ... else ... end if`)
- [ ] Loop (`while ... do ... end while`)
- [ ] Return statement

### Advanced Constructs
- [ ] Recursion
- [ ] Pattern matching (`match ... with ... end match`)
- [ ] Pipeline expressions (`|>`)
- [ ] Lambda functions (`fn(x) -> ...`)
- [ ] Option types (`Some`, `None`)
- [ ] Custom types
- [ ] List operations
- [ ] String operations

## 5. Solution Quality

### Geno Solutions
- Must be syntactically valid Geno
- Must pass the type checker
- Must include specification block (examples, requires, ensures)
- Should be idiomatic (use language features appropriately)
- Should demonstrate the intended construct

### Python Solutions
- Must be syntactically valid Python 3.10+
- Should use type hints
- Should be reasonably idiomatic
- Used as correctness baseline

## 6. Test Case Quality

### Functional Correctness
- Each test must have deterministic expected output
- Edge cases must be correctly specified
- Input/output types must match signature

### Coverage Criteria
- Empty/null inputs (where applicable)
- Single element inputs
- Typical cases
- Boundary values
- Negative numbers (for numeric types)
- Large inputs (for complexity testing)

## 7. Validation Process

Before adding a problem to the benchmark:

1. **Syntax Check**: Parse both solutions without errors
2. **Type Check**: Geno solution passes type checker
3. **Execution Check**: Both solutions produce same outputs for all test cases
4. **Review Check**: Human review for description clarity
5. **Difficulty Check**: Preliminary LLM testing to calibrate difficulty

## 8. Problem Independence

- Problems should be solvable independently
- No external dependencies or imports
- Self-contained problem descriptions
- Standard library functions only (documented in language spec)

## 9. Anti-Patterns to Avoid

### In Descriptions
- ❌ Implementation hints ("use a hash map")
- ❌ Ambiguous requirements
- ❌ Complex edge cases without specification
- ❌ Overly long descriptions

### In Test Cases
- ❌ Floating point comparisons without tolerance
- ❌ Order-dependent tests for unordered output
- ❌ Tests that timeout on correct solutions
- ❌ Tests with multiple valid answers (unless handled)

### In Solutions
- ❌ Overly clever/obfuscated code
- ❌ Unnecessary complexity
- ❌ Missing type annotations
- ❌ Solutions that don't demonstrate intended constructs

## 10. Metrics Collected

For each problem during evaluation:
- Parse success rate
- Type check success rate
- Visible test pass rate
- Hidden test pass rate
- Overall pass rate (all tests)
- Error category distribution
- Token count
- Execution time

## 11. Problem ID Schema

Format: `PROB-XXX` where XXX is a 3-digit number

Current problems span PROB-001 through PROB-077. There is no strict range-to-difficulty
mapping; IDs were assigned in order of creation. Current distribution:
- 12 trivial
- 32 easy
- 23 medium
- 7 hard
- 3 expert

## 12. Version Control

- Problems are versioned with the benchmark
- Changes to existing problems require version bump
- Deprecated problems are marked, not deleted
- Test result comparisons reference specific versions
