/** Offline declarations: generated from fresh Python during migration. */
export const CREATE = JSON.parse(`{
  "schema": {
    "$defs": {
      "BinaryExpression": {
        "additionalProperties": false,
        "properties": {
          "op": {
            "enum": [
              "add",
              "subtract",
              "multiply",
              "divide",
              "power",
              "min",
              "max"
            ],
            "title": "Op",
            "type": "string"
          },
          "left": {
            "$ref": "#/$defs/FormulaExpression"
          },
          "right": {
            "$ref": "#/$defs/FormulaExpression"
          }
        },
        "required": [
          "op",
          "left",
          "right"
        ],
        "title": "BinaryExpression",
        "type": "object"
      },
      "ConstantExpression": {
        "additionalProperties": false,
        "properties": {
          "op": {
            "const": "constant",
            "title": "Op",
            "type": "string"
          },
          "value": {
            "title": "Value",
            "type": "number"
          }
        },
        "required": [
          "op",
          "value"
        ],
        "title": "ConstantExpression",
        "type": "object"
      },
      "CountPeriod": {
        "enum": [
          "day",
          "week",
          "month"
        ],
        "title": "CountPeriod",
        "type": "string"
      },
      "ElapsedDaysExpression": {
        "additionalProperties": false,
        "properties": {
          "op": {
            "const": "elapsed_days",
            "title": "Op",
            "type": "string"
          },
          "start": {
            "title": "Start",
            "type": "string"
          }
        },
        "required": [
          "op",
          "start"
        ],
        "title": "ElapsedDaysExpression",
        "type": "object"
      },
      "FormulaExpression": {
        "anyOf": [
          {
            "$ref": "#/$defs/ConstantExpression"
          },
          {
            "$ref": "#/$defs/ReferenceExpression"
          },
          {
            "$ref": "#/$defs/BinaryExpression"
          },
          {
            "$ref": "#/$defs/SumDailyExpression"
          },
          {
            "$ref": "#/$defs/ElapsedDaysExpression"
          },
          {
            "$ref": "#/$defs/ReferenceAtExpression"
          },
          {
            "$ref": "#/$defs/IfBeforeExpression"
          }
        ]
      },
      "IfBeforeExpression": {
        "additionalProperties": false,
        "properties": {
          "op": {
            "const": "if_before",
            "title": "Op",
            "type": "string"
          },
          "date": {
            "title": "Date",
            "type": "string"
          },
          "before": {
            "$ref": "#/$defs/FormulaExpression"
          },
          "otherwise": {
            "$ref": "#/$defs/FormulaExpression"
          }
        },
        "required": [
          "op",
          "date",
          "before",
          "otherwise"
        ],
        "title": "IfBeforeExpression",
        "type": "object"
      },
      "MeasureKind": {
        "enum": [
          "reading",
          "running_total",
          "period_count"
        ],
        "title": "MeasureKind",
        "type": "string"
      },
      "MeasureOrigin": {
        "enum": [
          "taken",
          "assembled",
          "manual"
        ],
        "title": "MeasureOrigin",
        "type": "string"
      },
      "ReferenceAtExpression": {
        "additionalProperties": false,
        "properties": {
          "op": {
            "const": "reference_at",
            "title": "Op",
            "type": "string"
          },
          "measure_id": {
            "title": "Measure Id",
            "type": "string"
          },
          "date": {
            "title": "Date",
            "type": "string"
          }
        },
        "required": [
          "op",
          "measure_id",
          "date"
        ],
        "title": "ReferenceAtExpression",
        "type": "object"
      },
      "ReferenceExpression": {
        "additionalProperties": false,
        "properties": {
          "op": {
            "const": "reference",
            "title": "Op",
            "type": "string"
          },
          "measure_id": {
            "title": "Measure Id",
            "type": "string"
          },
          "day_offset": {
            "title": "Day Offset",
            "type": "integer"
          }
        },
        "required": [
          "op",
          "measure_id",
          "day_offset"
        ],
        "title": "ReferenceExpression",
        "type": "object"
      },
      "SumDailyExpression": {
        "additionalProperties": false,
        "description": "Sum dated numeric inputs in the explicit day, ISO week or calendar month.",
        "properties": {
          "op": {
            "const": "sum_daily",
            "title": "Op",
            "type": "string"
          },
          "measure_id": {
            "title": "Measure Id",
            "type": "string"
          },
          "period": {
            "$ref": "#/$defs/CountPeriod"
          }
        },
        "required": [
          "op",
          "measure_id",
          "period"
        ],
        "title": "SumDailyExpression",
        "type": "object"
      }
    },
    "additionalProperties": false,
    "description": "Wire input: omitted nullable fields retain the parser's None defaults.",
    "properties": {
      "project_id": {
        "title": "Project Id",
        "type": "string"
      },
      "name": {
        "title": "Name",
        "type": "string"
      },
      "unit": {
        "title": "Unit",
        "type": "string"
      },
      "origin": {
        "$ref": "#/$defs/MeasureOrigin"
      },
      "kind": {
        "$ref": "#/$defs/MeasureKind"
      },
      "timezone": {
        "title": "Timezone",
        "type": "string"
      },
      "period": {
        "anyOf": [
          {
            "$ref": "#/$defs/CountPeriod"
          },
          {
            "type": "null"
          }
        ]
      },
      "source_id": {
        "anyOf": [
          {
            "type": "string"
          },
          {
            "type": "null"
          }
        ],
        "title": "Source Id"
      },
      "formula": {
        "anyOf": [
          {
            "$ref": "#/$defs/FormulaExpression"
          },
          {
            "type": "null"
          }
        ]
      }
    },
    "required": [
      "project_id",
      "name",
      "unit",
      "origin",
      "kind",
      "timezone"
    ],
    "title": "CreateMeasureRequest",
    "type": "object"
  },
  "examples": {
    "taken_daily_count": {
      "project_id": "p_example",
      "name": "Daily activity count",
      "unit": "events",
      "origin": "taken",
      "kind": "period_count",
      "timezone": "UTC",
      "period": "day",
      "source_id": "source_example"
    }
  },
  "notes": [
    "Replace placeholder IDs with existing records and send this object to panels measure create on stdin. Omitted period, source_id and formula default to null.",
    "taken requires source_id and no formula; assembled requires a formula and no source_id; manual has neither. A source and its measure must share their Project.",
    "Kinds, units and period are descriptive metadata. The daily-count example uses kind period_count and period day; the writer does not require that pairing. timezone must be an IANA timezone. Dates are ISO calendar dates in that timezone.",
    "Formulas are expression objects, never provider SQL or Python. Existing graph, authority and definition checks apply after shape parsing."
  ]
}`) as Record<string, any>;
