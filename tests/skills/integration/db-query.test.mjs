// db-query.test.mjs — Integration test: create an infobase and execute a read-only COM query

export const name = 'Read-only запрос через COMConnector';
export const setup = 'none';
export const requiresPlatform = true;

export const steps = [
  {
    name: 'db-create: пустая файловая ИБ',
    script: 'db-create/scripts/db-create',
    args: {
      '-V8Path': '{v8path}',
      '-InfoBasePath': '{workDir}/testdb',
    },
  },
  {
    name: 'writeFile: параметризованный запрос',
    writeFile: '{workDir}/query.sql',
    content: 'ВЫБРАТЬ &Значение КАК Значение',
  },
  {
    name: 'writeFile: параметры запроса',
    writeFile: '{workDir}/params.json',
    content: '{"Значение": 42}',
  },
  {
    name: 'db-query: JSON-результат',
    script: 'db-query/scripts/db-query',
    args: {
      '-InfoBasePath': '{workDir}/testdb',
      '-QueryFile': '{workDir}/query.sql',
      '-ParametersJson': '{workDir}/params.json',
      '-OutputFile': '{workDir}/result.json',
    },
  },
  {
    name: 'assertContains: число строк',
    assertContains: '{workDir}/result.json',
    expect: '"rowCount"',
  },
  {
    name: 'assertContains: значение параметра',
    assertContains: '{workDir}/result.json',
    expect: '"Значение"',
  },
  {
    name: 'assertContains: число 42',
    assertContains: '{workDir}/result.json',
    expect: '42',
  },
];
