import { resolve } from 'node:path';
const corpus = process.env.DRAFTWRIGHT_CORPUS ?? '/Users/paul/repos/quiddity-python/tests/corpus';
export const localParts = {
  plate: resolve('tests/fixtures/parts/mounting_plate.step'),
  m10127: resolve(corpus, 'mfcadpp/10127.step'),
  grm03: resolve(corpus, 'gramel/GRM-03_thumbwheel_drive_screw.step'),
  ctc01: resolve(corpus, 'nist/nist_ctc_01_asme1_rd.stp')
};
export const featureNames = {
  plate: 'plate', m10127: '10127', grm03: 'GRM-03_thumbwheel_drive_screw', ctc01: 'nist_ctc_01_asme1_rd'
};
