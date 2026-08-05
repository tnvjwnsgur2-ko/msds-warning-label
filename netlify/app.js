const MAX_FILES = 10;
const API_BASE_URL = String(window.MSDS_API_BASE_URL || '').replace(/\/+$/, '');

function apiUrl(path) {
  const value = String(path || '');
  if (/^https?:\/\//i.test(value)) return value;
  const normalised = value.startsWith('/') ? value : `/${value}`;
  return `${API_BASE_URL}${normalised}`;
}

const DOCUMENT_CONFIGS = {
  warning: {
    mode: 'warning',
    modules: [
      { id: 'W-1', label: '제품명', field: 'product_name' },
      { id: 'W-2', label: '그림문자', field: 'pictograms' },
      { id: 'W-3', label: '신호어', field: 'signal_word' },
      { id: 'W-4', label: '유해·위험 문구', field: 'hazard_statements' },
      { id: 'W-5', label: '예방조치 문구', field: 'precautionary_statements' },
      { id: 'W-6', label: '공급자 정보', field: 'supplier_information' },
    ],
    endpoint: '/api/warning-labels',
    saveEndpoint: '/api/warning-labels/save',
    responseKey: 'warning_label',
    collectionKey: 'labels',
    templateId: 'warningEditorTemplate',
    editorEyebrow: 'WARNING LABEL EDITOR',
    editorTitle: '경고표지 편집',
    editorSubtitle: '자동 발췌된 W-1~W-6 내용을 검토하고 필요한 부분을 직접 수정하세요.',
    resultTitle: 'PDF별 W-1~W-6 결과',
    processTitle: 'W-1부터 W-6까지 실행하고 있습니다.',
    processDescription: '제품명, 그림문자, 신호어, 유해·위험 문구, 예방조치 문구, 공급자 정보를 발췌합니다.',
    completedText: 'W-1~W-6 실행 완료',
    printTitle: 'MSDS_경고표지',
    requiresWorkName: false,
  },
  management: {
    mode: 'management',
    modules: [
      { id: 'M-1', label: '제품명', field: 'product_name' },
      { id: 'M-2', label: '건강 및 환경 유해성·물리적 위험성', field: 'hazard_risk_summary' },
      { id: 'M-3', label: '안전 및 보건상의 취급주의 사항', field: 'safe_handling_precautions' },
      { id: 'M-4', label: '적절한 보호구', field: 'personal_protective_equipment' },
      { id: 'M-5', label: '응급조치 요령 및 사고 시 대처방법', field: 'emergency_response' },
    ],
    endpoint: '/api/management-guides',
    saveEndpoint: '/api/management-guides/save',
    responseKey: 'management_guide',
    collectionKey: 'guides',
    templateId: 'managementEditorTemplate',
    editorEyebrow: 'MANAGEMENT GUIDE EDITOR',
    editorTitle: '관리요령 편집',
    editorSubtitle: '자동 생성된 M-1~M-5 내용을 검토하고 작업명과 각 항목을 직접 수정하세요.',
    resultTitle: 'PDF별 M-1~M-5 결과',
    processTitle: 'M-1부터 M-5까지 실행하고 있습니다.',
    processDescription: '제품명, 유해성·위험성, 취급주의 사항, 보호구, 응급조치 및 사고대처 내용을 생성합니다.',
    completedText: 'M-1~M-5 실행 완료',
    printTitle: 'MSDS_관리요령',
    requiresWorkName: true,
  },
};

const uploadView = document.getElementById('uploadView');
const editorView = document.getElementById('editorView');
const input = document.getElementById('pdfInput');
const dropZone = document.getElementById('dropZone');
const fileCounter = document.getElementById('fileCounter');
const fileArea = document.getElementById('fileArea');
const fileList = document.getElementById('fileList');
const message = document.getElementById('message');
const clearAllButton = document.getElementById('clearAllButton');
const warningButton = document.getElementById('warningButton');
const guideButton = document.getElementById('guideButton');
const processPanel = document.getElementById('processPanel');
const processTitle = document.getElementById('processTitle');
const processDescription = document.getElementById('processDescription');
const editorEyebrow = document.getElementById('editorEyebrow');
const editorTitle = document.getElementById('editorTitle');
const editorSubtitle = document.getElementById('editorSubtitle');
const resultTitle = document.getElementById('resultTitle');
const resultSummary = document.getElementById('resultSummary');
const resultList = document.getElementById('resultList');
const editorMessage = document.getElementById('editorMessage');
const backButton = document.getElementById('backButton');
const saveButton = document.getElementById('saveButton');
const printButton = document.getElementById('printButton');

let selectedFiles = [];
let latestResponse = null;
let currentMode = null;
let isProcessing = false;
let isSaving = false;
let pictogramCatalog = [];
const workNamesByFile = new Map();

function currentConfig() {
  return currentMode ? DOCUMENT_CONFIGS[currentMode] : null;
}

function isPdf(file) {
  return file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
}

function fileKey(file) {
  return `${file.name}-${file.size}-${file.lastModified}`;
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function setMessage(element, text, type = 'error') {
  element.textContent = text;
  element.className = `message is-visible ${type}`;
}

function clearMessage(element) {
  element.textContent = '';
  element.className = 'message';
}

function showUploadView() {
  editorView.hidden = true;
  uploadView.hidden = false;
  clearMessage(editorMessage);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function showEditorView() {
  uploadView.hidden = true;
  editorView.hidden = false;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function setProcessing(value, title = '', description = '') {
  isProcessing = value;
  processPanel.hidden = !value;
  warningButton.disabled = value || selectedFiles.length === 0;
  guideButton.disabled = value || selectedFiles.length === 0;
  clearAllButton.disabled = value;
  input.disabled = value;

  if (value) {
    processTitle.textContent = title;
    processDescription.textContent = description;
  }
}

function invalidateGeneratedResults() {
  latestResponse = null;
  currentMode = null;
  resultList.innerHTML = '';
}

function addFiles(fileCollection) {
  if (isProcessing) return;
  clearMessage(message);

  const incoming = Array.from(fileCollection);
  const invalidFiles = incoming.filter((file) => !isPdf(file));
  const validFiles = incoming.filter(isPdf);
  const existingKeys = new Set(selectedFiles.map(fileKey));
  const uniqueFiles = validFiles.filter((file) => !existingKeys.has(fileKey(file)));
  const duplicateCount = validFiles.length - uniqueFiles.length;
  const availableSlots = MAX_FILES - selectedFiles.length;
  const acceptedFiles = uniqueFiles.slice(0, availableSlots);

  selectedFiles = [...selectedFiles, ...acceptedFiles];
  acceptedFiles.forEach((file) => {
    if (!workNamesByFile.has(fileKey(file))) workNamesByFile.set(fileKey(file), '');
  });
  if (acceptedFiles.length > 0) invalidateGeneratedResults();

  if (uniqueFiles.length > availableSlots) {
    setMessage(message, `PDF는 최대 ${MAX_FILES}개까지 업로드할 수 있습니다. 초과 파일은 제외했습니다.`);
  } else if (invalidFiles.length > 0) {
    setMessage(message, `PDF가 아닌 파일 ${invalidFiles.length}개는 제외했습니다.`);
  } else if (duplicateCount > 0) {
    setMessage(message, `중복 파일 ${duplicateCount}개는 제외했습니다.`);
  } else if (acceptedFiles.length > 0) {
    setMessage(message, `${acceptedFiles.length}개 PDF를 추가했습니다.`, 'success');
  }

  input.value = '';
  renderFiles();
}

function removeFile(index) {
  if (isProcessing) return;
  workNamesByFile.delete(fileKey(selectedFiles[index]));
  selectedFiles.splice(index, 1);
  invalidateGeneratedResults();
  clearMessage(message);
  renderFiles();
}

function renderFiles() {
  fileList.innerHTML = '';

  selectedFiles.forEach((file, index) => {
    const item = document.createElement('li');
    item.className = 'file-item';

    const chip = document.createElement('span');
    chip.className = 'pdf-chip';
    chip.textContent = 'PDF';

    const info = document.createElement('div');
    info.className = 'file-info';

    const name = document.createElement('div');
    name.className = 'file-name';
    name.textContent = file.name;
    name.title = file.name;

    const size = document.createElement('div');
    size.className = 'file-size';
    size.textContent = formatFileSize(file.size);

    const workNameField = document.createElement('label');
    workNameField.className = 'file-work-name';
    const workNameLabel = document.createElement('span');
    workNameLabel.textContent = '관리요령 작업명 (파일별 필수)';
    const workNameInput = document.createElement('input');
    workNameInput.type = 'text';
    workNameInput.maxLength = 200;
    workNameInput.placeholder = '이 PDF의 작업명';
    workNameInput.value = workNamesByFile.get(fileKey(file)) || '';
    workNameInput.setAttribute('aria-label', `${file.name}의 관리요령 작업명`);
    workNameInput.addEventListener('input', () => {
      workNamesByFile.set(fileKey(file), workNameInput.value);
      clearMessage(message);
    });
    workNameField.append(workNameLabel, workNameInput);

    const remove = document.createElement('button');
    remove.className = 'remove-button';
    remove.type = 'button';
    remove.setAttribute('aria-label', `${file.name} 삭제`);
    remove.textContent = '×';
    remove.disabled = isProcessing;
    remove.addEventListener('click', () => removeFile(index));

    info.append(name, size);
    item.append(chip, info, workNameField, remove);
    fileList.appendChild(item);
  });

  const hasFiles = selectedFiles.length > 0;
  fileArea.hidden = !hasFiles;
  fileCounter.textContent = `${selectedFiles.length} / ${MAX_FILES}`;
  warningButton.disabled = isProcessing || !hasFiles;
  guideButton.disabled = isProcessing || !hasFiles;
}

function buildFormData() {
  const formData = new FormData();
  selectedFiles.forEach((file) => formData.append('files', file, file.name));
  return formData;
}

async function readError(response) {
  if (response.status === 504) {
    return 'PDF 처리 시간이 길어 서버 응답이 지연됐습니다. 잠시 후 다시 시도하거나 파일 수를 줄여주세요.';
  }
  try {
    const data = await response.json();
    return data.detail || data.error || `요청 실패 (${response.status})`;
  } catch {
    return `요청 실패 (${response.status})`;
  }
}

function emptyModules(config) {
  return config.modules.map((definition) => ({
    module_id: definition.id,
    label: definition.label,
    field: definition.field,
    text: '',
    matched: false,
    pages: [],
  }));
}

function normaliseModules(item, config) {
  const received = item[config.responseKey]?.modules || [];
  const byId = new Map(received.map((module) => [module.module_id, module]));
  return emptyModules(config).map((fallback) => ({ ...fallback, ...(byId.get(fallback.module_id) || {}) }));
}

function autoResize(textarea) {
  textarea.style.height = 'auto';
  textarea.style.height = `${Math.max(textarea.scrollHeight, 54)}px`;
}

function previewValue(value) {
  return value.trim() || '입력되지 않음';
}

function updateWorkNamePreview(card) {
  const input = card.querySelector('input[data-field="work_name"]');
  const preview = card.querySelector('.preview-work-name');
  if (!input || !preview) return;
  preview.textContent = previewValue(input.value);
  preview.classList.toggle('preview-empty', !input.value.trim());
}

function updateCardPreview(card) {
  const config = currentConfig();
  if (!config) return;

  config.modules.forEach(({ field }) => {
    const textarea = card.querySelector(`textarea[data-field="${field}"]`);
    const preview = card.querySelector(`.preview-${field}`);
    if (textarea && preview) {
      preview.textContent = previewValue(textarea.value);
      preview.classList.toggle('preview-empty', !textarea.value.trim());
    }
  });
  if (config.mode === 'warning') {
    const imagePreview = card.querySelector('.preview-pictogram-images');
    if (imagePreview) {
      imagePreview.innerHTML = '';
      card.querySelectorAll('.pictogram-choice input:checked').forEach((checkbox) => {
        const asset = pictogramCatalog.find((item) => item.id === checkbox.value);
        if (!asset) return;
        const image = document.createElement('img');
        image.src = apiUrl(asset.url);
        image.alt = asset.label;
        image.title = asset.label;
        imagePreview.appendChild(image);
      });
    }
  }
  if (config.requiresWorkName) updateWorkNamePreview(card);
}

function renderPictogramPicker(card, module) {
  const picker = card.querySelector('.pictogram-picker');
  if (!picker) return;
  const selected = new Set((module?.pictogram_assets || []).map((asset) => asset.id));
  pictogramCatalog.forEach((asset) => {
    const label = document.createElement('label');
    label.className = 'pictogram-choice';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = asset.id;
    checkbox.checked = selected.has(asset.id);
    const image = document.createElement('img');
    image.src = apiUrl(asset.url);
    image.alt = '';
    const name = document.createElement('span');
    name.textContent = asset.label;
    checkbox.addEventListener('change', () => updateCardPreview(card));
    label.append(checkbox, image, name);
    picker.appendChild(label);
  });
}

function createProgressChip(module, extractionFailed) {
  const chip = document.createElement('span');
  chip.className = 'module-chip';
  const status = extractionFailed ? '실행 실패' : module.matched ? '추출됨' : '미발견';
  chip.textContent = `${module.module_id} ${status}`;
  chip.classList.add(extractionFailed ? 'failed' : module.matched ? 'matched' : 'missing');
  if (module.pages?.length) chip.title = `${module.pages.join(', ')}페이지에서 발췌`;
  return chip;
}

function renderEditorCard(item, index, config) {
  const template = document.getElementById(config.templateId);
  const fragment = template.content.cloneNode(true);
  const card = fragment.querySelector('.editor-card');
  const extractionFailed = item.status !== 'success';
  const modules = normaliseModules(item, config);

  card.dataset.index = String(index);
  card.dataset.sourceFile = item.source_file || `document-${index + 1}.pdf`;
  card.dataset.pageCount = String(item.page_count || 0);
  card.dataset.originalStatus = item.status || 'error';

  const status = fragment.querySelector('.result-status');
  status.textContent = extractionFailed ? '수동 입력 필요' : config.completedText;
  status.classList.add(extractionFailed ? 'error' : 'success');
  fragment.querySelector('.result-file-name').textContent = card.dataset.sourceFile;
  fragment.querySelector('.result-page-count').textContent = item.page_count ? `${item.page_count}페이지` : '';
  fragment.querySelector('.print-source').textContent = `원본 PDF: ${card.dataset.sourceFile}`;

  const error = fragment.querySelector('.result-error');
  if (extractionFailed) {
    error.hidden = false;
    error.textContent = `자동 처리 실패: ${item.error || '원인을 확인할 수 없습니다.'} 빈 입력란에 직접 작성할 수 있습니다.`;
  }

  const progress = fragment.querySelector('.module-progress');
  modules.forEach((module) => progress.appendChild(createProgressChip(module, extractionFailed)));

  modules.forEach((module) => {
    const textarea = fragment.querySelector(`textarea[data-field="${module.field}"]`);
    if (!textarea) return;
    textarea.value = module.text || '';
    textarea.dataset.moduleId = module.module_id;
    textarea.addEventListener('input', () => {
      autoResize(textarea);
      updateCardPreview(card);
      clearMessage(editorMessage);
    });
  });

  if (config.mode === 'warning') {
    renderPictogramPicker(card, modules.find((module) => module.module_id === 'W-2'));
  } else if (config.requiresWorkName) {
    const workName = card.querySelector('input[data-field="work_name"]');
    const sourceFile = selectedFiles[index];
    if (sourceFile) {
      card.dataset.fileKey = fileKey(sourceFile);
      workName.value = workNamesByFile.get(card.dataset.fileKey) || '';
    }
    workName.setAttribute('aria-label', `${card.dataset.sourceFile}의 작업명`);
    workName.addEventListener('input', () => {
      if (card.dataset.fileKey) workNamesByFile.set(card.dataset.fileKey, workName.value);
      updateWorkNamePreview(card);
      clearMessage(editorMessage);
    });
  }

  resultList.appendChild(fragment);
  const appendedCard = resultList.lastElementChild;
  appendedCard.querySelectorAll('textarea').forEach(autoResize);
  updateCardPreview(appendedCard);
}

function renderResults(response, config) {
  resultList.innerHTML = '';
  editorEyebrow.textContent = config.editorEyebrow;
  editorTitle.textContent = config.editorTitle;
  editorSubtitle.textContent = config.editorSubtitle;
  resultTitle.textContent = config.resultTitle;
  response.results.forEach((item, index) => renderEditorCard(item, index, config));
  resultSummary.textContent = `${response.requested_count}개 PDF · ${config.modules[0].id}~${config.modules.at(-1).id} 전체 실행 · 자동 처리 성공 ${response.success_count}개 · 수동 입력 필요 ${response.failure_count}개`;
  showEditorView();
}

async function startGeneration(mode) {
  if (selectedFiles.length === 0 || isProcessing) return;
  const config = DOCUMENT_CONFIGS[mode];
  currentMode = mode;

  clearMessage(message);
  setProcessing(
    true,
    config.processTitle,
    `${selectedFiles.length}개 PDF에서 ${config.processDescription}`,
  );

  try {
    if (mode === 'warning' && pictogramCatalog.length === 0) {
      const catalogResponse = await fetch(apiUrl('/api/pictograms'));
      if (catalogResponse.ok) pictogramCatalog = (await catalogResponse.json()).assets || [];
    }
    const response = await fetch(apiUrl(config.endpoint), {
      method: 'POST',
      body: buildFormData(),
    });
    if (!response.ok) throw new Error(await readError(response));

    latestResponse = await response.json();
    renderResults(latestResponse, config);
  } catch (error) {
    const localFileHint = window.location.protocol === 'file:'
      ? ' index.html을 직접 열지 말고 FastAPI 서버로 실행해야 합니다.'
      : '';
    setMessage(message, `${error.message || '서버 연결에 실패했습니다.'}${localFileHint}`);
    currentMode = null;
  } finally {
    setProcessing(false);
    renderFiles();
  }
}

function collectEditedRecords(config) {
  return Array.from(resultList.querySelectorAll('.editor-card')).map((card) => {
    const modules = config.modules.map((definition) => {
      const textarea = card.querySelector(`textarea[data-field="${definition.field}"]`);
      const result = {
        module_id: definition.id,
        label: definition.label,
        field: definition.field,
        text: textarea?.value.trim() || '',
      };
      if (definition.id === 'W-2') {
        result.pictogram_assets = Array.from(card.querySelectorAll('.pictogram-choice input:checked'))
          .map((checkbox) => pictogramCatalog.find((asset) => asset.id === checkbox.value))
          .filter(Boolean);
      }
      return result;
    });

    const finalFields = Object.fromEntries(modules.map((module) => [module.field, module.text]));
    const record = {
      source_file: card.dataset.sourceFile,
      source_page_count: Number(card.dataset.pageCount || 0),
      extraction_status: card.dataset.originalStatus,
      modules,
      final_fields: finalFields,
    };
    if (config.requiresWorkName) {
      record.work_name = card.querySelector('input[data-field="work_name"]')?.value.trim() || '';
    }
    return record;
  });
}

function validateWorkNames(config) {
  if (!config.requiresWorkName) return true;
  const missingInput = Array.from(resultList.querySelectorAll('input[data-field="work_name"]'))
    .find((input) => !input.value.trim());
  if (!missingInput) return true;

  const card = missingInput.closest('.editor-card');
  const warningText = `${card?.dataset.sourceFile || 'PDF'}의 작업명을 입력해야 저장하거나 인쇄할 수 있습니다.`;
  window.alert(warningText);
  setMessage(editorMessage, warningText);
  missingInput.focus();
  missingInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
  return false;
}

async function saveEditedResults() {
  const config = currentConfig();
  if (!config || isSaving || resultList.children.length === 0) return;
  if (!validateWorkNames(config)) return;

  isSaving = true;
  saveButton.disabled = true;
  clearMessage(editorMessage);
  resultList.querySelectorAll('.editor-card').forEach(updateCardPreview);

  const payload = {
    [config.collectionKey]: collectEditedRecords(config),
  };
  try {
    const response = await fetch(apiUrl(config.saveEndpoint), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(await readError(response));

    const saved = await response.json();
    setMessage(editorMessage, `${saved.message} 파일 다운로드를 시작합니다.`, 'success');

    const anchor = document.createElement('a');
    anchor.href = apiUrl(saved.download_url);
    anchor.download = saved.filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } catch (error) {
    setMessage(editorMessage, error.message || '저장 중 오류가 발생했습니다.');
  } finally {
    isSaving = false;
    saveButton.disabled = false;
  }
}

function printEditedResults() {
  const config = currentConfig();
  if (!config || resultList.children.length === 0) return;
  if (!validateWorkNames(config)) return;

  resultList.querySelectorAll('.editor-card').forEach((card) => {
    card.querySelectorAll('textarea').forEach(autoResize);
    updateCardPreview(card);
  });

  const previousTitle = document.title;
  document.title = config.printTitle;
  window.print();
  document.title = previousTitle;
}

input.addEventListener('change', (event) => addFiles(event.target.files));

['dragenter', 'dragover'].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    if (!isProcessing) dropZone.classList.add('is-dragging');
  });
});

['dragleave', 'drop'].forEach((eventName) => {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove('is-dragging');
  });
});

dropZone.addEventListener('drop', (event) => addFiles(event.dataTransfer.files));
dropZone.addEventListener('keydown', (event) => {
  if ((event.key === 'Enter' || event.key === ' ') && !isProcessing) {
    event.preventDefault();
    input.click();
  }
});

clearAllButton.addEventListener('click', () => {
  if (isProcessing) return;
  selectedFiles = [];
  workNamesByFile.clear();
  invalidateGeneratedResults();
  clearMessage(message);
  renderFiles();
});

warningButton.addEventListener('click', () => startGeneration('warning'));
guideButton.addEventListener('click', () => startGeneration('management'));
backButton.addEventListener('click', showUploadView);
saveButton.addEventListener('click', saveEditedResults);
printButton.addEventListener('click', printEditedResults);
renderFiles();
