CXX      ?= g++
CXXFLAGS ?= -std=c++17 -O3 -Wall -Wextra -fPIC
LDFLAGS  ?= -shared

TARGET   := libsteiner.so
SRC      := libsteiner.cpp
OBJ      := $(SRC:.cpp=.o)

.PHONY: all clean check test

all: $(TARGET)

$(TARGET): $(OBJ)
	$(CXX) $(LDFLAGS) -o $@ $^

%.o: %.cpp
	$(CXX) $(CXXFLAGS) -c -o $@ $<

check test: $(TARGET)
	./paragon_optimize.py tests/eternal_rogue_sample.json >/dev/null
	python3 -m unittest discover -s tests -v

clean:
	$(RM) $(TARGET) $(OBJ)
