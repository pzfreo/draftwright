# Recognised helical springs

Recognised springs default to the ISO 2162-1 simplified outline, end coils and centreline. The free length and outer diameter are dimensioned. The spring data table retains wire and mean diameters, free length, active/total coils with their reported bases, hand and end type. Active coils remain unknown when the recogniser reports null. Rate appears only when explicitly stated.

Use the spec/3 feature override to draw the complete wire geometry:

```json
{"schema":"draftwright.spec/3","features":{"f:helical_spring:f0":{"spring":"full"}}}
```

`features/<feature-id>/spring` accepts `"simplified"` or `"full"`. It applies only to that recognised spring. Remove the field to restore the default. The normal Spec edit operations support setting and removing it. Full depiction retains the data table and the two spring dimensions; it does not create individual wire-segment hole, boss or fillet requirements.

The simplified isometric uses the same closed outline and end coils as the principal views. Its silhouette is included in the existing furniture bounds. Authored details clip spring ink to their requested region.
