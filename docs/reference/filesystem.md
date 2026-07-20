# Filesystem Metadata and Canonicalization

Geno exposes three capability-gated filesystem inspection operations:

```geno
fs_metadata(path: String) -> Result[FileMetadata, String]
fs_symlink_metadata(path: String) -> Result[FileMetadata, String]
fs_canonicalize(path: String) -> Result[String, String]
```

Their built-in data types are:

```geno
type FileKind = FileKindFile
              | FileKindDirectory
              | FileKindSymlink
              | FileKindOther

type FileMetadata = FileMetadata(
    kind: FileKind,
    size: Int,
    modified_ms: Int,
)
```

`fs_metadata` follows symbolic links and reports metadata for the resolved
target. `fs_symlink_metadata` resolves parent components but does not follow the
final component. A final symbolic link is therefore reported as
`FileKindSymlink`, including when its target is missing. `size` is the host
filesystem's byte-size field and `modified_ms` is the modification timestamp in
whole milliseconds since the Unix epoch, rounded down. The byte size of a
non-regular file is host-defined.

`fs_canonicalize` requires an existing path, resolves `.` and `..` components
and all symbolic links, and returns an absolute path with `/` separators. On
Windows, drive paths use `C:/...` and UNC paths use `//server/share/...`.

A canonical path is a comparison and reporting value, not an authorization token.
The default hosted and compiled Python policy still rejects absolute path inputs;
applications should retain the original scoped path for later filesystem calls, or
a trusted host may explicitly enable absolute paths within configured roots.

All three functions require the `fs` capability and are unavailable on the
browser target. Embedding through `geno.api.run()` never grants ambient
filesystem access: the host must explicitly provide callbacks for these names.
Hosted and compiled Python execution apply the same configured filesystem-root
policy as the existing `fs_*` operations. Final links may be inspected inside a
root, but followed metadata and canonicalization reject links that resolve
outside it. Compiled Node artifacts retain Geno's existing trusted-runtime
filesystem model; the `fs` capability grants host filesystem access there.

Missing paths, inaccessible paths, dangling canonicalization targets, and
filesystem-root policy rejections return `Err(message)`. Capability denial and
portable integer or resource-limit violations remain runtime errors. Existing
`fs_exists` behavior is unchanged.

Canonical paths use the portable Path string format. `path_parent`,
`path_filename`, and `path_extension` therefore compose with them, and
`path_is_absolute` recognizes both leading-slash paths and Windows drive paths
such as `C:/work/file`.
