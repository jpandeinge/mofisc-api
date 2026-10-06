# Transaction model artifacts

The trained Mofisc transaction classifier and merchant index are not included
in the public repository. They may contain proprietary training output and are
distributed only through controlled deployment storage.

The API can run without them. For public development, use:

```dotenv
LOCAL_MODEL_ENABLED=0
LOCAL_MODEL_REQUIRED=0
PFC_MODEL_REQUIRED=0
```

Production operators who provide a compatible artifact can set
`LOCAL_MODEL_DIR` and use the `production-local-model` Docker target. Only load
pickle-compatible artifacts from a trusted source and verify their checksums
before deserialisation.
