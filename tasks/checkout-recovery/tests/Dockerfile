FROM harbor-native-runtime:spike
COPY . /tests
RUN python3 /tests/install.py
ENV AUTOWFBENCH_ROOT=/tests/vendor/autowfbench PYTHONPATH=/opt/native:/tests:/tests/vendor/autowfbench
