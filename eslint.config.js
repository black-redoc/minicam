// @ts-check
const js = require("@eslint/js");
const globals = require("globals");

module.exports = [
  js.configs.recommended,
  {
    files: ["app/static/js/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: {
        ...globals.browser,
      },
    },
    rules: {
      "no-var": "error",
    },
  },
  {
    // stream.js, ptz.js, config.js, admin.js all consume authedFetch (and
    // ptz.js also consumes SessionExpiredError), both defined in auth.js.
    // Declaring these here (not in auth.js's own config block) avoids
    // no-redeclare/no-unused-vars false positives in the defining file.
    files: [
      "app/static/js/stream.js",
      "app/static/js/ptz.js",
      "app/static/js/config.js",
      "app/static/js/admin.js",
    ],
    languageOptions: {
      globals: {
        authedFetch: "readonly",
        SessionExpiredError: "readonly",
      },
    },
  },
  {
    // config.js and admin.js both consume confirmDialog, defined in
    // confirm-dialog.js.
    files: ["app/static/js/config.js", "app/static/js/admin.js"],
    languageOptions: {
      globals: {
        confirmDialog: "readonly",
      },
    },
  },
  {
    // grid.js consumes VideoStream, defined in stream.js.
    files: ["app/static/js/grid.js"],
    languageOptions: {
      globals: {
        VideoStream: "readonly",
      },
    },
  },
];
